state={}
def update(item):state[item['id']]={'text':item['text'],'version':item['version'],'start':3,'end':4,'chunk_id':f"{item['id']}:1:0"}
def search(query):return [{"id":k,**v} for k,v in state.items() if query in v['text']]
