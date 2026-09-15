#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,subprocess
from pathlib import Path
import g3_negative_controls_v3 as v3

g=v3.g
AMENDMENT_SHA='33c53b01bd437eca666adaf8f812df33d65a2bdb'
POS_HEAD='ec1213c9d954d4f4e40929096a6e16bcd1c75707'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def wr(p,o):p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,indent=2,sort_keys=True)+'\n')
def cvc5(bin_,p):
 q=subprocess.run([bin_,str(p)],capture_output=True,text=True,timeout=120);got=(q.stdout.strip().splitlines() or [''])[0].strip();return got,q.returncode,(q.stdout+q.stderr)[-2000:]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--base-dir',required=True);ap.add_argument('--old-source',required=True);ap.add_argument('--new-source',required=True);ap.add_argument('--calibration',required=True);ap.add_argument('--positive',required=True);ap.add_argument('--cvc5',required=True);ap.add_argument('--ethos',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.out);root.mkdir(parents=True,exist_ok=True)
 cal=json.loads(Path(a.calibration).read_text())
 cal_ok=bool(cal.get('pass') and len(cal.get('rows',[]))==16 and all(r.get('pass') for r in cal['rows']) and all(not z.get('unsupported_reachable') for z in cal.get('reachable_audit',{}).values()))
 if not cal_ok:raise RuntimeError('Amendment07 calibration/support audit RED')
 n3=g.nc3(root);q3=Path(n3['query']['path']);got,rc,txt=cvc5(a.cvc5,q3);n3pass=bool(n3['validator_rejected_mutant'] and n3['replay']['divergent'] and n3['one_bit_corruption']['rejected'] and rc==0 and got.lower()==str(n3['semantic_result']).lower())
 if not n3pass:raise RuntimeError('NC3 RED '+txt)
 n4=g.nc4(root,Path(a.base_dir),a.old_source,a.new_source);n4pass=bool(n4['rejected'])
 if not n4pass:raise RuntimeError('NC4 RED')
 pos=Path(a.positive)
 if (pos/'runner.commit.txt').read_text().strip()!=POS_HEAD:raise RuntimeError('positive authority head drift')
 rows=list(csv.DictReader((pos/'METAMORPHIC_CERTIFICATES.tsv').open(),delimiter='\t'))
 if len(rows)!=120 or not all(x['status']=='UNSAT_CERTIFIED_CPC' for x in rows):raise RuntimeError('positive certificate matrix drift')
 x=sorted(rows,key=lambda r:(r['lane'],r['label'],r['family']))[0]
 if (x['lane'],x['label'],x['family'])!=('semantic_equivalence','mirror_new','cell_order'):raise RuntimeError('NC6 source selection drift')
 src=pos/'proofs'/(x['lane']+'__'+x['label']+'__'+x['family']+'.cpc');dst=root/'nc6-corrupted.cpc';lines=src.read_text().splitlines();changed=False
 for i in range(len(lines)-1,-1,-1):
  if lines[i].startswith('(step ') and ' false :rule ' in lines[i]:lines[i]=lines[i].replace(' false :rule ',' true :rule ',1);changed=True;break
 if not changed:raise RuntimeError('no terminal false goal for NC6')
 dst.write_text('\n'.join(lines)+'\n');q=subprocess.run([a.ethos,str(dst.resolve())],cwd=pos,capture_output=True,text=True,timeout=120);eth=(q.stdout+q.stderr);n6pass=not(q.returncode==0 and q.stdout.strip()=='correct')
 if not n6pass:raise RuntimeError('NC6 corrupted proof accepted')
 out={'schema':'g3-amendment08-unchanged-controls-v1','authority':'PROSPECTIVE_AMENDMENT08_AUTHORITY','amendment08_freeze_sha':AMENDMENT_SHA,'calibration':{'pass':cal_ok,'rows':16,'unsupported_reachable_total':sum(len(z.get('unsupported_reachable',[])) for z in cal['reachable_audit'].values()),'sha256':sha(a.calibration)},'nc3':{'pass':n3pass,'semantic_result':n3['semantic_result'],'query_sha256':n3['query']['sha256'],'cvc5':got,'validator_rejected_mutant':n3['validator_rejected_mutant'],'canonical_divergent':n3['replay']['divergent'],'corruption_rejected':n3['one_bit_corruption']['rejected']},'nc4':{'pass':n4pass,'selected':n4.get('selected')},'nc6':{'pass':n6pass,'selected':x,'source_proof_sha256':sha(src),'corrupted_proof_sha256':sha(dst),'ethos_returncode':q.returncode,'ethos_tail':eth[-3000:]},'pass':cal_ok and n3pass and n4pass and n6pass}
 wr(root/'CONTROLS.json',out);print(json.dumps({'calibration':cal_ok,'nc3':n3pass,'nc4':n4pass,'nc6':n6pass,'pass':out['pass']},sort_keys=True))
if __name__=='__main__':main()
