INDEX=[{"id":"old","text":"legacy fact","embedding_version":"v1"}]
def search(query,embedding_version):
 if embedding_version!='v1':raise ValueError('embedding version requires migration')
 return INDEX
