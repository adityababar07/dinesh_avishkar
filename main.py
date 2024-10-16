from machine import I2S
from machine import Pin
import time
import socket

HOST = "192.168.101.98"  # The server's hostname or IP address
PORT = 40674   # The port used by the server
switch0 = Pin(19, Pin.OUT)


s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind((HOST, PORT))

s.listen()
# Establish connection with client.


while True:
    c, addr = s.accept()
    print('Got connection from', addr )
    data = c.recv(2048)
    print(data.decode('utf-8'))
    c.send(b'Thank you for connecting')
    c.close()
    #18 is IN1
    #19 is IN2
    if "on" in data:
        switch0.on()
    elif "off" in data:
        switch0.off()
