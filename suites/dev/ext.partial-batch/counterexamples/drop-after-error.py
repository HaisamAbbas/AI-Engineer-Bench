def process(items):
 results=[];failures=[]
 for item in items:
  if not isinstance(item.get('text'),str): failures.append({'id':item.get('id'),'status':'malformed'});break
  results.append({'id':item['id'],'value':item['text'].upper()})
 return {'results':results,'failures':failures}
