"""Credential-free unittest execution with a Python socket egress guard."""
import argparse
import json
import os
import sys
import time
import unittest
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]))
p.add_argument('--pattern',default='test*.py')
p.add_argument('--output',required=True)
a=p.parse_args()
root=Path(a.root).resolve()
for name in list(os.environ):
    if any(s in name.upper() for s in ('API_KEY','INTERNAL_TOKEN','TELNYX_WEBHOOK','PROVIDER_CREDENTIAL')):
        os.environ.pop(name,None)
observed={'loopback_connects':0,'blocked_external_connects':0}
def guard(event,args):
    if event=='socket.connect':
        address=args[1]
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):
            observed['blocked_external_connects']+=1
            raise PermissionError('Offline audit blocked non-loopback socket')
        observed['loopback_connects']+=1
sys.addaudithook(guard)
# Establish tokenizer search paths before a test temporarily clears os.environ.
# No automatic download or provider connection is permitted by this runner.
os.environ['NLTK_DATA']=str(root/'nltk_data')
import nltk.data
nltk.data.path.insert(0,str(root/'nltk_data'))
sys.path.insert(0,str(root/'server'))
sys.path.insert(0,str(root))
class Result(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.rows=[]
    def addSuccess(self,test):
        super().addSuccess(test); self.rows.append({'test':test.id(),'status':'PASS'})
    def addFailure(self,test,err):
        super().addFailure(test,err); self.rows.append({'test':test.id(),'status':'FAIL','detail':self._exc_info_to_string(err,test)})
    def addError(self,test,err):
        super().addError(test,err); self.rows.append({'test':test.id(),'status':'ERROR','detail':self._exc_info_to_string(err,test)})
    def addSkip(self,test,reason):
        super().addSkip(test,reason); self.rows.append({'test':test.id(),'status':'SKIP','reason':reason})
start=time.perf_counter()
suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern=a.pattern)
result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(suite)
receipt={'scope':'OFFLINE_APPLICATION_TESTS_NOT_LIVE_CARRIER','root':str(root), 'tests_run':result.testsRun,
         'passed':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors),
         'skipped':len(result.skipped),'seconds':time.perf_counter()-start,'socket_guard':observed,'tests':result.rows}
destination=Path(a.output)
destination.parent.mkdir(parents=True,exist_ok=True)
destination.write_text(json.dumps(receipt,indent=2))
print(json.dumps({k:v for k,v in receipt.items() if k!='tests'},indent=2))
sys.exit(0 if result.wasSuccessful() else 1)
