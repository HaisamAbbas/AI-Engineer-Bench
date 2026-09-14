PENDING={}
def run(job):
 session=job['session_id']
 if job['action'] in {'draft','correct'}:PENDING[session]=job['target'];return {'status':'awaiting-confirmation' if job['action']=='draft' else 'corrected'}
 return {'status':'executed','target':PENDING.pop(session,None)}
