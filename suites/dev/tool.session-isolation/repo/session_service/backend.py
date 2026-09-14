LAST={}
def run(job):
 global LAST
 if job.get('action')=='set': LAST={'value':job['value']};return {'status':'saved'}
 return {'status':'ok','value':LAST.get('value')}
