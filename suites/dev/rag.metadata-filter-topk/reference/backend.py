DOCS=[{"id":"a","text":"alpha common","team":"red","score":9},{"id":"b","text":"beta common","team":"red","score":8},{"id":"c","text":"target common","team":"blue","score":7}]
def search(query,top_k,metadata=None):
 hits=[d for d in DOCS if query in d["text"] and (not metadata or all(d.get(k)==v for k,v in metadata.items()))]
 return sorted(hits,key=lambda d:-d["score"])[:top_k]
