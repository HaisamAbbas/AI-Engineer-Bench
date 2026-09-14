def run(job, sessions={}):
 state=sessions.setdefault(job['session_id'],{})
 if job.get('action')=='set':state['value']=job['value'];return {'status':'saved'}
 return {'status':'ok','value':state.get('value')}
