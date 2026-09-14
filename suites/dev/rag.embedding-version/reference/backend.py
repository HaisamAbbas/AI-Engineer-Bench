INDEX=[{"id":"old","text":"legacy fact","embedding_version":"v1"}]
def search(query,embedding_version):return [x for x in INDEX if x['embedding_version']==embedding_version]
