"""Continue the frozen real20 at merge; only the thinking switch changes."""
import os,sys,json,time,hashlib,argparse,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'work/real20-20261110'
parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');args=parser.parse_args()
os.environ['ICT_MERGE_THINKING']='1' if args.verify else '0'
SNAP=BASE/'source_snapshot'
sys.path[:0]=[str(SNAP/'src'),str(ROOT/'eval')]
import ict.config as config
for name,value in list(vars(config).items()):
 if isinstance(value,Path) and value.is_relative_to(SNAP):setattr(config,name,ROOT/value.relative_to(SNAP))
from evaluate_front_half_20261108 import Recorded,read,write
from ict.schemas import AnnouncementUnderstanding,CandidateFile,MergeEvidence
from ict.steps.s08_merge import merge_projects
from ict.steps.s08b_repair import repair_packages
from ict.steps.s09_supplier import complete_suppliers
from ict.supplier_policy import finalize_suppliers
from ict.pipeline import _rejected_supplier_fields
from ict.llm import LLMClient,LLMResult
from ict.concurrency import CallCounter
OUT=ROOT/'work/real20-no-thinking-20261110'
manifest=read(BASE/'manifest.json');cfg=config.llm_settings();assert cfg['model']==manifest['model']
class Replay(LLMClient):
 def __init__(self,aid):
  self.saved={}
  for p in sorted((BASE/'calls'/aid).glob('*.json')):
   r=read(p);key=(r['step'],r['prompt_version'],r['user'],r.get('thinking',False));self.saved[key]=r
 def complete(self,*,step,prompt_version,user,thinking=False):
  key=(step,prompt_version,user,thinking)
  assert key in self.saved,f'Frozen input differs: {step}/{prompt_version}'
  r=self.saved[key];assert 'result' in r
  return LLMResult(**r['result'])
def inputs(aid):
 folder=BASE/'runs'/aid
 original=read(folder/'01_announcement_understanding.json');binding=read(folder/'03b_package_bindings.json') if (folder/'03b_package_bindings.json').exists() else {}
 ann=AnnouncementUnderstanding.model_validate(binding.get('effective_understanding',original))
 normalized=CandidateFile.model_validate(read(folder/'08_normalized_candidates.json'))
 att=CandidateFile.model_validate(read(folder/'07_attachment_extraction.json'))
 for item in binding.get('observation_bindings',[]):
  i=int(item['amount_ref'].rsplit(':',1)[1]);obs=att.package_amounts[i];obs.binding=item
  if item.get('package_no'):obs.package_no=item['package_no']
  obs.amount_kind=item['amount_kind']
 for item in binding.get('unresolved',[]):
  if item.get('reason')=='binding_model_failed' and item.get('amount_ref','').startswith('amount:attachment:'):
   att.package_amounts[int(item['amount_ref'].rsplit(':',1)[1])].binding=item
 html=CandidateFile(run_id=ann.run_id,status=ann.status,reception=[{'branch':branch,**data['output']['reception']} for branch,data in original['branch_audit'].items() if isinstance(data,dict) and 'reception' in data.get('output',{})])
 evidence=MergeEvidence.model_validate(read(folder/'08b_merge_evidence.json'))
 return ann,normalized,att,html,evidence

def run(aid):
 if not args.verify and (OUT/'runs'/aid/'10_run_report.json').exists():return read(OUT/'case_metrics'/f'{aid}.json')
 ann,norm,att,html,evidence=inputs(aid);counter=CallCounter();client=Replay(aid) if args.verify else Recorded(cfg,OUT/'calls'/aid)
 t=time.perf_counter();merged=merge_projects(ann.run_id,ann,norm.candidates,att.package_amounts,evidence=evidence,llm=client,counter=counter);merge_s=time.perf_counter()-t
 merged.unresolved.extend(_rejected_supplier_fields(html,ann));merged.unresolved.extend(_rejected_supplier_fields(att,ann))
 repair_failures=repair_packages(merged,evidence.winner_hints,client,counter);merged.failures.extend(repair_failures)
 supplier_failures=complete_suppliers(merged,client,counter);merged.failures.extend(supplier_failures)
 if supplier_failures:merged.status="partial"
 finalize_suppliers(merged)
 result={'schema_version':'announcement-v2','announcement_id':aid,'project_name':ann.project_name,'source_project_no':ann.source_project_no,'purchaser':ann.purchaser,'announcement_type':ann.announcement_type,'summary_amount':ann.summary_amount.model_dump() if ann.summary_amount else None,'packages':[p.model_dump(exclude={'provenance'}) for p in merged.projects]}
 if args.verify:
  assert result==read(BASE/'runs'/aid/'projects.json'),f'Checkpoint continuation differs: {aid}'
  return {'id':aid,'frozen_output_equal':True}
 duration=time.perf_counter()-t;folder=OUT/'runs'/aid
 write(folder/'projects.json',result);write(folder/'09_merged_projects.json',merged.model_dump())
 # Upstream artifacts are references/copies of the original fixed results, not rerun services.
 for name in ['01_announcement_understanding.json','04_project_plans.json','07_attachment_extraction.json','08_normalized_candidates.json','08b_merge_evidence.json']:
  write(folder/name,read(BASE/'runs'/aid/name))
 failures=list(merged.failures);counts={'projects':len(merged.projects),'final_cobs':sum(len(p.cobs) for p in merged.projects),'final_subs':sum(len(p.subs) for p in merged.projects)}
 write(folder/'10_run_report.json',{'status':merged.status,'duration_ms':int(duration*1000),'counts':counts,'failure_summary':[{'failure_code':code,'count':sum(x.failure_code==code for x in failures)} for code in sorted({x.failure_code for x in failures})],'upstream_reused':True,'duration_scope':'merge_and_downstream_only','model_calls':counter.model_calls()})
 metrics={'id':aid,'merge_seconds':round(merge_s,3),'tail_seconds':round(duration,3),'status':merged.status,'counts':counts}
 write(OUT/'case_metrics'/f'{aid}.json',metrics);return metrics

def main():
 start=time.perf_counter()
 if not args.verify:
  record={**manifest,'source_run':str(BASE),'experiment':'fixed upstream; v8 frozen source; MERGE_THINKING=0; downstream real API','no_ocr':True,'no_database':True,'start_time':time.strftime('%Y-%m-%d %H:%M:%S')}
  if not (OUT/'manifest.json').exists():write(OUT/'manifest.json',record)
 completed=[]
 with ThreadPoolExecutor(max_workers=config.concurrency_settings()['announcement_workers']) as pool:
  for f in as_completed([pool.submit(run,aid) for aid in manifest['ids']]):
   row=f.result();completed.append(row);print(json.dumps({'completed':len(completed),**row},ensure_ascii=False),flush=True)
 if args.verify:write(OUT/'checkpoint_verification.json',{'all_equal':True,'cases':completed,'api_calls':0,'ocr_calls':0})
 else:write(OUT/'experiment_summary.json',{'cases':completed,'batch_tail_seconds':time.perf_counter()-start,'thinking':False,'prompt':'merge-objects-v8','scope':'merge_and_downstream_only','ocr_calls':0})
if __name__=='__main__':main()
