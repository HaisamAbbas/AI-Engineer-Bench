def run(job, pending={}):
 if job['action']!='execute':pending[job['session_id']]=job['target'];return {'status':'corrected'}
 return {'status':'executed','target':pending.pop(job['session_id'],None)}
