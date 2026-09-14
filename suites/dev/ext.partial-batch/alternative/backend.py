def process(items):
 valid=[item for item in items if isinstance(item.get('text'),str)]
 bad=[item for item in items if not isinstance(item.get('text'),str)]
 return {'results':[{'id':item['id'],'value':item['text'].upper()} for item in valid],'failures':[{'id':item.get('id'),'status':'malformed'} for item in bad]}
