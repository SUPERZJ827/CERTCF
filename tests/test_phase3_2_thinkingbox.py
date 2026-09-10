from scripts.phase3_2_thinkingbox_r2a import identity, verify_population

def record(n=0, task='t'):
 return {'task_uid':f'f:{task}','task_id':task,'domain':'d','target_call':{'index':n},'target_argument':{'name':'x'},'original_value':n,'mutation_value':n+1,'state_effect':{'type':'E.x'},'original_effect_witness':{},'mutation_rule_version':'v'}

def test_population_identity_contains_frozen_fields():
 assert set(identity(record()))=={'task_id','domain','target_call','target_argument','original_value','mutation_value','state_effect','original_effect_witness','mutation_rule_version'}

def test_population_rejects_wrong_size():
 assert not verify_population([record()])
