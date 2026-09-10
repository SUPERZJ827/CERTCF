from scripts.phase4_3_scan_r3v2 import boundary, evaluate

def test_token_boundary_does_not_match_open_in_unopened():
 assert not boundary('the box is unopened','open')

def test_stable_projection_ignores_unrelated_fields():
 predicate={'task_relevant_clauses':[{'operation':'update','table':'t','entity_id':'1','expected_fields':{'assignee':'A'}}]}
 state={'t':[{'id':'1','assignee':'A','updated_at':'later'}]}
 assert evaluate(predicate,state)['satisfied']
