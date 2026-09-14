state={}
def update(item):state[item['id']]=(item['version'],item['text'])
def search(query):return [{"id":i,"version":v,"text":t,"start":0,"end":len(t),"chunk_id":f"{i}:{v}:0"} for i,(v,t) in state.items() if query in t]
