SESSIONS={}
def run(job):
 state=SESSIONS.setdefault(job['session_id'],{})
 if job.get('action')=='set':state['value']=job['value'];return {'status':'saved'}
 return {'status':'ok','value':state.get('value')}
