import socket   
import sys  
import time

# Gegenstelle ("Capture Agent")
host = 'localhost';
port = 8888;

# Horchen auf
myhost = 'localhost';
myport = 8889;

def startNetwork():
  global srcv
  global ssnd
  srcv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  print ('Socket created')
  
  
  srcv.bind((myhost, myport))
  
  
  ssnd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  ssnd.connect((host, port))
  print ('Socket bind complete')

 
startNetwork()

while(1) :
    msg = input('Message to Capture Agent: ')
     
    ssnd.sendall(msg.encode('UTF-8'))
    
    time.sleep(0.1)
#    d = srcv.recvfrom(256)
#    indata = d[0]
#    addr = d[1]
#    print(indata.decode('UTF-8'))        
