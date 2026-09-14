def process(record):
 factor={'g':1,'kg':1000,'mg':.001}[record['unit']];return {'id':record['id'],'grams':str(float(record['value'])*factor),'evidence':record['value'],'unit':record['unit'],'label':record.get('label')}
