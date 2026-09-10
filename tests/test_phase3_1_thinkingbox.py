from scripts.phase3_1_thinkingbox_r2a import exact, mutate, queues, field_changes

def sample(task='t', index=0):
 return {'task_uid':'f.py:'+task,'task_id':task,'target_call':{'index':index},'target_argument':{'name':'name'},'original_value':'A','mutation_value':'B','mutation_rule_version':'v1','state_effect':{'type':'Entity.name'},'reference_trajectory_digest':'0'*64}

def test_exact_one_argument_mutation():
 original=[{'name':'create','arguments':{'name':'A','keep':1}}]
 changed=[{'name':'create','arguments':mutate(original[0]['arguments'],'$.name','B')}]
 assert exact(original,changed,0,'name','A','B')['status']=='MUTATION_EXACTNESS_SUCCESS'

def test_task_diverse_queue_is_deterministic():
 records=[sample('a',1),sample('a',2),sample('b',0)]
 assert queues(records)==queues(records)
 assert len(queues(records)[2])==2

def test_effect_witness_uses_persistent_row_field():
 before={'items':[]}; after={'items':[{'id':'1','name':'B'}]}
 assert field_changes(before,after,'name')==[{'table':'items','entity_id':'1','field':'name','before':None,'after':'B'}]
