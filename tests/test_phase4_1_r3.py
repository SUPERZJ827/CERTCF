from scripts.phase4_1_thinkingbox_r3 import eval_predicate, exact

def test_effect_predicate_create_and_update():
 state={'t':[{'id':'1','status':'done','value':3}]}
 create={'effect_id':'e','clauses':[{'operation':'create','table':'t','entity_id':'1','expected_record':state['t'][0]}]}
 update={'effect_id':'u','clauses':[{'operation':'update','table':'t','entity_id':'1','expected_fields':{'status':'done'}}]}
 assert eval_predicate(create,state)['satisfied'] and eval_predicate(update,state)['satisfied']

def test_exact_omission_only_deletes_frozen_call():
 calls=[{'name':'a','arguments':{}},{'name':'b','arguments':{'x':1}}]
 cert=exact(calls,[calls[0]],1,calls[1])
 assert cert['status']=='OMISSION_EXACTNESS_CERTIFIED'
