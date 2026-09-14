from decimal import Decimal
FACTORS={'g':Decimal('1'),'kg':Decimal('1000'),'mg':Decimal('0.001')}
def process(record):
 value=Decimal(str(record['value']));return {'id':record['id'],'grams':str(value*FACTORS[record['unit']]),'evidence':record['value'],'unit':record['unit'],'label':record.get('label')}
