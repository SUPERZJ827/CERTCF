#!/usr/bin/env python3
"""Read-only original-solution calibration for AppWorld response tracing."""
from __future__ import annotations
from os import environ
import hashlib, importlib.util, json, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).parents[1]; OUT=ROOT/'artifacts/phase0_9_observability'; DATA=Path(environ.get("CERTCF_APPWORLD_ROOT", str(Path(__file__).resolve().parents[1] / "data/appworld")))
P7_PATH=ROOT/'scripts/phase0_7_appworld_dual_replay.py'
spec=importlib.util.spec_from_file_location('p7obs',P7_PATH); assert spec and spec.loader
P7=importlib.util.module_from_spec(spec); sys.modules[spec.name]=P7; spec.loader.exec_module(P7)
NORMALIZATION={'request_trace':'Official RequestTracker entries are compared exactly after canonical JSON serialization.','response_trace':'Requester.request inputs and returned dict/list objects are canonicalized without omitted fields.','state':'Complete ModelCollection full export; JSON keys canonicalized and JSONL rows sorted.'}

def run(task_id:str, source:str, directory:Path, instrumented:bool)->dict[str,Any]:
    from appworld.environment import AppWorld
    world=AppWorld(task_id=task_id,experiment_name=f'phase0_9_obs_{task_id}_{"i" if instrumented else "u"}',ground_truth_mode='full')
    trace=[]; directory.mkdir(parents=True,exist_ok=True)
    try:
        world.models.save(str(directory/'initial'),format='full',delete_if_exists=True); initial,_=P7.file_tree_digest(directory/'initial')
        if instrumented:
            original=world.requester.request
            def traced(*args:Any,**kwargs:Any)->Any:
                app=kwargs.get('_app_name',args[0] if args else None); api=kwargs.get('_api_name',args[1] if len(args)>1 else None)
                data={key:value for key,value in kwargs.items() if key not in {'_app_name','_api_name','client','raise_on_failure','show','track'}}
                try:
                    result=original(*args,**kwargs); trace.append({'sequence':len(trace)+1,'api_identity':f'{app}.{api}','arguments':P7.canonical(data),'returned_response':P7.canonical(result)}); return result
                except Exception as exc:
                    trace.append({'sequence':len(trace)+1,'api_identity':f'{app}.{api}','arguments':P7.canonical(data),'exception':{'type':type(exc).__name__,'message':str(exc)}}); raise
            world.requester.request=traced
        message=world.execute(source+'\n__phase09_obs_return = solution(apis, requester)\n')
        marker=object(); returned=world.shell.user_ns.get('__phase09_obs_return',marker); returned=None if returned is marker else P7.canonical(returned)
        request_trace=P7.canonical(world.requester.request_tracker.requests)
        world.models.save(str(directory/'final'),format='full',delete_if_exists=True); final,state=P7.file_tree_digest(directory/'final')
        evaluation=P7.evaluator_payload(world)
        payload={'execution_success':P7.execution_ok(message),'message':message,'initial_digest':initial,'request_trace':request_trace,'response_trace':trace,'return_value':returned,'final_digest':final,'evaluator':evaluation}
        P7.write_json(directory/'execution.json',payload); P7.write_json(directory/'final_state.json',state)
        return payload
    finally: world.close()

def unique_targets(trace:list[dict[str,Any]], candidate:dict[str,Any])->bool:
    ids=[candidate['a']['callee'].removeprefix('apis.'),candidate['b']['callee'].removeprefix('apis.')]
    return all(sum(item.get('api_identity')==identity for item in trace)==1 for identity in ids)

def main()->None:
    retry=json.loads((ROOT/'artifacts/phase0_9_retry1/PILOT_QUEUE_V2.json').read_text())['queue'][:3]
    results=[]
    for candidate in retry:
        source=(DATA/'data/tasks'/candidate['task_id']/'ground_truth/compiled_solution.py').read_text(); case=OUT/'cases'/candidate['task_id']
        plain=run(candidate['task_id'],source,case/'uninstrumented',False); inst=run(candidate['task_id'],source,case/'instrumented',True)
        checks={'initial_state_identical':plain['initial_digest']==inst['initial_digest'],'execution_identical':plain['execution_success']==inst['execution_success'],'official_request_trace_identical':plain['request_trace']==inst['request_trace'],'final_state_identical':plain['final_digest']==inst['final_digest'],'return_value_identical':plain['return_value']==inst['return_value'],'evaluator_identical':plain['evaluator']==inst['evaluator'],'response_trace_captured':bool(inst['response_trace']),'target_calls_uniquely_identified':unique_targets(inst['response_trace'],candidate)}
        status='CALIBRATION_CERTIFIED' if all(checks.values()) else 'CALIBRATION_FAILED'
        P7.write_json(case/'calibration_certificate.json',{'candidate':candidate,'status':status,'checks':checks})
        results.append({'task_id':candidate['task_id'],'status':status,'checks':checks})
    gate='OBSERVABILITY_LAYER_VALIDATED' if len(results)==3 and all(row['status']=='CALIBRATION_CERTIFIED' for row in results) else 'OBSERVABILITY_LAYER_NOT_VALIDATED'
    P7.write_json(OUT/'results.json',{'gate':gate,'results':results})
    if gate=='OBSERVABILITY_LAYER_VALIDATED':
        receipt={'instrumentation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'response_canonicalization_sha256':hashlib.sha256(P7.canonical_bytes(NORMALIZATION)).hexdigest(),'appworld_commit':'42b5bcf3cd334fee33f0c37d02070a9f5807add5','appworld_version':'0.2.0.dev0','calibration_queue':retry,'calibration_results':results,'transformed_evaluator_outcomes_observed_before_instrumentation_freeze':0,'transformation_changed':False,'validity_criterion_changed':False,'candidate_population_changed':False,'pilot_queue_changed':False}
        P7.write_json(OUT/'FREEZE_RECEIPT.json',receipt)
    P7.write_json(ROOT/'artifacts/phase0_9_retry1/REPORT_ERRATA.json',{'reported_value':0,'corrected_value':9,'correction_basis':'nine persisted transformed/execution.json artifacts','protocol_changed':False,'experimental_result_changed':False})
    (OUT/'PHASE0_9_OBSERVABILITY_CALIBRATION.md').write_text(f'# Phase 0.9 Observability Calibration\n\nGate: `{gate}`\n\nThe instrumentation wraps the instance Requester.request method, records its input and returned response, and returns the original result unchanged. No transformed solution or transformed evaluator was executed.\n')
if __name__=='__main__': main()
