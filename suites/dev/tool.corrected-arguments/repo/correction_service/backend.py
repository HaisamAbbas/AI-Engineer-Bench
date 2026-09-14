PENDING={}
def run(job):
 session=job['session_id']
 if job['action']=='draft':PENDING[session]=job['target'];return {'status':'awaiting-confirmation'}
 if job['action']=='correct':return {'status':'corrected'}
 return {'status':'executed','target':PENDING.get(session)}
