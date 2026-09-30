#!/usr/bin/env python3
from pathlib import Path
import argparse, json, re
import pandas as pd

ENTRY_RE=re.compile(r'^\s*(openml__.+?__(\d+))\s*$')
TASK_RE=re.compile(r'__(\d+)$')

def parse_task_id(name):
    m=TASK_RE.search(str(name)); return int(m.group(1)) if m else None

def parse_list(path, label):
    rows=[]
    for i,line in enumerate(Path(path).read_text().splitlines(),1):
        m=ENTRY_RE.match(line)
        if m:
            full=m.group(1); task=int(m.group(2)); base=re.sub(r'__\d+$','',full)
            rows.append({'source_list':label,'source_line':i,'dataset_name':full,'task_id':task,'base_name':base})
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--a',required=True); ap.add_argument('--b',required=True)
    ap.add_argument('--raw',required=True); ap.add_argument('--clean',required=True); ap.add_argument('--out',required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    study=pd.DataFrame(parse_list(a.a,'A')+parse_list(a.b,'B'))
    study.to_csv(out/'study_population_entries.csv',index=False)
    raw=pd.read_csv(a.raw,usecols=['dataset_name']); clean=pd.read_csv(a.clean,usecols=['dataset_name'])
    raw['task_id']=raw.dataset_name.map(parse_task_id); clean['task_id']=clean.dataset_name.map(parse_task_id)
    if raw.task_id.isna().any() or clean.task_id.isna().any(): raise RuntimeError('unparseable task IDs in result CSV')
    raw_ids=set(raw.task_id.astype(int)); clean_ids=set(clean.task_id.astype(int)); study_ids=set(study.task_id.astype(int))
    # Duplicate task entries and base-name collisions are different identity notions.
    dup_task=study[study.duplicated('task_id',keep=False)].sort_values(['task_id','source_list','source_line'])
    dup_task.to_csv(out/'duplicate_study_task_entries.csv',index=False)
    base_coll=study.groupby('base_name').filter(lambda x:x.task_id.nunique()>1).sort_values(['base_name','task_id'])
    base_coll.to_csv(out/'study_base_name_collisions.csv',index=False)
    def rows_for(ids, name):
        z=study[study.task_id.isin(sorted(ids))].copy().sort_values('task_id'); z.to_csv(out/name,index=False); return z
    miss_raw=study_ids-raw_ids; extra_raw=raw_ids-study_ids; miss_clean=study_ids-clean_ids; extra_clean=clean_ids-study_ids
    rows_for(miss_raw,'study_tasks_missing_from_raw.csv')
    rows_for(miss_clean,'study_tasks_missing_from_cleaned.csv')
    pd.DataFrame({'task_id':sorted(extra_raw)}).to_csv(out/'raw_tasks_not_in_study_list.csv',index=False)
    pd.DataFrame({'task_id':sorted(extra_clean)}).to_csv(out/'cleaned_tasks_not_in_study_list.csv',index=False)
    summary={
      'study_list_entries':int(len(study)),
      'study_unique_task_ids':int(study.task_id.nunique()),
      'study_unique_dataset_names_with_task_suffix':int(study.dataset_name.nunique()),
      'study_unique_base_names':int(study.base_name.nunique()),
      'study_duplicate_task_entry_rows':int(len(dup_task)),
      'study_base_collision_rows':int(len(base_coll)),
      'raw_unique_task_ids':int(len(raw_ids)),
      'cleaned_unique_task_ids':int(len(clean_ids)),
      'study_tasks_missing_from_raw_count':int(len(miss_raw)),
      'study_tasks_missing_from_raw_ids':sorted(miss_raw),
      'raw_tasks_not_in_study_list_count':int(len(extra_raw)),
      'raw_tasks_not_in_study_list_ids':sorted(extra_raw),
      'study_tasks_missing_from_cleaned_count':int(len(miss_clean)),
      'cleaned_tasks_not_in_study_list_count':int(len(extra_clean)),
      'cleaned_tasks_not_in_study_list_ids':sorted(extra_clean),
      'cleaned_is_subset_of_raw':bool(clean_ids.issubset(raw_ids)),
      'raw_is_subset_of_study_list':bool(raw_ids.issubset(study_ids)),
      'cleaned_is_subset_of_study_list':bool(clean_ids.issubset(study_ids)),
    }
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
