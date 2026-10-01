#!/usr/bin/env python3
import json,time
from importlib.metadata import version
from typing_extensions import TypedDict
from langgraph.graph import StateGraph,START,END
from langgraph.checkpoint.postgres import PostgresSaver

class S(TypedDict):
    source_marker:str
    clean_marker:str
    derived:str

def step(s:S): return {'derived':'D['+s['source_marker']+']|K['+s['clean_marker']+']'}

def main():
    conn='postgresql://postgres:localpass@127.0.0.1:5432/postgres'
    last=None
    for _ in range(60):
        try:
            with PostgresSaver.from_conn_string(conn) as saver:
                saver.setup()
                b=StateGraph(S); b.add_node('step',step); b.add_edge(START,'step'); b.add_edge('step',END)
                g=b.compile(checkpointer=saver); cfg={'configurable':{'thread_id':'pg-case'}}
                g.invoke({'source_marker':'R_src','clean_marker':'K_src','derived':''},cfg)
                before=list(saver.list(cfg))
                g.update_state(cfg,{'source_marker':'','derived':'K[K_src]'})
                latest=g.get_state(cfg).values
                hist=list(saver.list(cfg))
                texts=[json.dumps(t.checkpoint,sort_keys=True,default=str) for t in hist]
                rev=sum('R_src' in x for x in texts); clean=sum('K_src' in x for x in texts)
                saver.delete_thread('pg-case')
                rem=list(saver.list(cfg))
                out={'langgraph_version':version('langgraph'),'checkpoint_postgres_version':version('langgraph-checkpoint-postgres'),
                  'checkpoint_count_before_repair':len(before),'checkpoint_count_after_current_repair':len(hist),
                  'latest_has_revoked':'R_src' in json.dumps(latest,sort_keys=True),'latest_has_clean':'K_src' in json.dumps(latest,sort_keys=True),
                  'history_with_revoked':rev,'history_with_clean':clean,'after_delete_thread':len(rem)}
                print('RESULT',json.dumps(out,sort_keys=True))
                assert not out['latest_has_revoked'] and out['latest_has_clean']
                assert rev>0 and len(rem)==0
                return
        except Exception as e:
            last=e; time.sleep(.5)
    raise last
if __name__=='__main__':main()
