def process(items):
    """Broken: a malformed item aborts all otherwise valid work."""
    if any(not isinstance(item.get('text'),str) for item in items): raise ValueError('malformed model item')
    return {'results':[{'id':item['id'],'value':item['text'].upper()} for item in items],'failures':[]}
