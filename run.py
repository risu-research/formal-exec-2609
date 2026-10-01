#!/usr/bin/env python3
import json,time,urllib.request
from importlib.metadata import version
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

def req(method,url,obj=None):
    data=None if obj is None else json.dumps(obj).encode()
    r=urllib.request.Request(url,data=data,method=method,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(r,timeout=20) as x:
        b=x.read().decode(); return json.loads(b) if b else None

def wait(url):
    for _ in range(100):
        try:
            with urllib.request.urlopen(url,timeout=2) as r:
                if r.status<500:return
        except Exception:time.sleep(.3)
    raise RuntimeError('wait '+url)

def setup_chroma():
    base='http://127.0.0.1:8000/api/v2'; wait(base+'/heartbeat')
    p=base+'/tenants/default_tenant/databases/default_database/collections'
    c=req('POST',p,{'name':'jpipe','get_or_create':True}); root=p+'/'+c['id']
    req('POST',root+'/add',{'ids':['src-rev','src-clean'],'documents':['R_src','K_src'],'embeddings':[[1.,0.],[0.,1.]],'metadatas':[{'dep':'rev'},{'dep':'clean'}]})
    got=req('POST',root+'/get',{'ids':['src-rev'],'include':['documents','metadatas']})
    marker=got['documents'][0]
    return root,marker

def setup_qdrant():
    base='http://127.0.0.1:6333'; wait(base+'/')
    req('PUT',base+'/collections/jpipe',{'vectors':{'size':2,'distance':'Cosine'}})
    req('PUT',base+'/collections/jpipe/points?wait=true',{'points':[
      {'id':1,'vector':[1.,0.],'payload':{'name':'src-rev','text':'R_src','dep':'rev'}},
      {'id':2,'vector':[0.,1.],'payload':{'name':'src-clean','text':'K_src','dep':'clean'}}]})
    got=req('POST',base+'/collections/jpipe/points',{'ids':[1],'with_payload':True,'with_vector':False})
    marker=got['result'][0]['payload']['text']
    return base,marker

class S(TypedDict):
    ch_marker:str
    q_marker:str
    clean_marker:str
    derived:str

def step(s:S):
    return {'derived':'C['+s['ch_marker']+']|Q['+s['q_marker']+']|K['+s['clean_marker']+']'}

def main():
    croot,cm=setup_chroma(); qbase,qm=setup_qdrant()
    assert cm=='R_src' and qm=='R_src'
    saver=InMemorySaver(); b=StateGraph(S); b.add_node('step',step); b.add_edge(START,'step'); b.add_edge('step',END)
    g=b.compile(checkpointer=saver); cfg={'configurable':{'thread_id':'case-1'}}
    g.invoke({'ch_marker':cm,'q_marker':qm,'clean_marker':'K_src','derived':''},cfg)
    state_before=g.get_state(cfg).values
    hist_before=list(saver.list(cfg))

    # Legal source revocation occurs after both stores have supplied the source.
    req('POST',croot+'/delete',{'ids':['src-rev']})
    req('POST',qbase+'/collections/jpipe/points/delete?wait=true',{'points':[1]})
    cg=req('POST',croot+'/get',{'ids':['src-rev'],'include':['documents']})
    qs=req('POST',qbase+'/collections/jpipe/points',{'ids':[1],'with_payload':True,'with_vector':False})
    retrieval_clean=(len(cg['ids'])==0 and len(qs['result'])==0)
    state_after_source_delete=g.get_state(cfg).values

    # precise current-state repair, but do not rewrite history
    g.update_state(cfg,{'ch_marker':'','q_marker':'','derived':'K[K_src]'})
    current_repaired=g.get_state(cfg).values
    hist_after=list(saver.list(cfg))
    texts=[json.dumps(t.checkpoint,sort_keys=True,default=str) for t in hist_after]
    hist_rev=sum('R_src' in t for t in texts)
    hist_clean=sum('K_src' in t for t in texts)

    # only native coarse cleanup guaranteed by InMemorySaver
    saver.delete_thread('case-1')
    rem=list(saver.list(cfg))
    out={
      'langgraph_version':version('langgraph'),
      'retrieval_clean_after_native_source_delete':retrieval_clean,
      'state_before_contains_revoked':'R_src' in json.dumps(state_before,sort_keys=True),
      'current_state_after_source_delete_contains_revoked':'R_src' in json.dumps(state_after_source_delete,sort_keys=True),
      'current_state_after_precise_repair_contains_revoked':'R_src' in json.dumps(current_repaired,sort_keys=True),
      'current_state_after_precise_repair_contains_clean':'K_src' in json.dumps(current_repaired,sort_keys=True),
      'checkpoint_count_before':len(hist_before),
      'checkpoint_count_after_current_repair':len(hist_after),
      'historical_checkpoints_with_revoked_marker_after_current_repair':hist_rev,
      'historical_checkpoints_with_clean_marker_after_current_repair':hist_clean,
      'thread_delete_remaining_checkpoints':len(rem),
      'thread_delete_removed_clean_history_too':len(rem)==0 and hist_clean>0}
    print('RESULT',json.dumps(out,sort_keys=True))
    assert retrieval_clean
    assert out['current_state_after_source_delete_contains_revoked']
    assert not out['current_state_after_precise_repair_contains_revoked']
    assert out['historical_checkpoints_with_revoked_marker_after_current_repair']>0
    assert out['thread_delete_remaining_checkpoints']==0
if __name__=='__main__':main()
