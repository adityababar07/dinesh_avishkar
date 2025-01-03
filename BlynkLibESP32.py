#!/usr/bin/env python3

# Micro Python library that brings out-of-the-box Blynk support to
# the WiPy. Requires a previously established internet connection
# and a valid token string.
#
# Example usage:
#
#     import BlynkLib
#     import time
#
#     blynk = BlynkLib.Blynk('08a46fbc7f57407995f576f3f84c3f72')
#
#     # define a virtual pin read handler
#     def v0_read_handler():
#         # we must call virtual write in order to send the value to the widget
#         blynk.virtual_write(0, time.ticks_ms() // 1000)
#
#     # register the virtual pin
#     blynk.add_virtual_pin(0, read=v0_read_handler)
#
#     # define a virtual pin write handler
#     def v1_write_handler(value):
#         print(value)
#
#     # register the virtual pin
#     blynk.add_virtual_pin(1, write=v1_write_handler)
#
#     # register the task running every 3 sec
#     # (period must be a multiple of 50 ms)
#     def my_user_task():
#         # do any non-blocking operations
#         print('Action')
#
#     blynk.set_user_task(my_user_task, 3000)
#
#     # start Blynk (this call should never return)
#     blynk.run()
#
# -----------------------------------------------------------------------------
#
# This file is part of the Micro Python project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2015 Daniel Campora
# Copyright (c) 2015 Volodymyr Shymanskyy
# Copyright (c) 2019 Mauro Riva - Wipy 2.0/3.0 & ESP32 support
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.


import socket
import struct
import time
import os

import machine
import errno

import gc

# try:
#     import pycom as wipy                #WIPY3.0
# except:
#   pass

try:
    from micropython import const       #ESP32
except:
  pass

HDR_LEN = const(5)
HDR_FMT = "!BHH"

MAX_MSG_PER_SEC = const(20)

MSG_RSP = const(0)
MSG_LOGIN = const(2)
MSG_PING = const(6)
MSG_TWEET = const(12)
MSG_EMAIL = const(13)
MSG_NOTIFY = const(14)
MSG_BRIDGE = const(15)
MSG_HW_SYNC = const(16)
MSG_HW_INFO = const(17)
MSG_HW = const(20)

STA_SUCCESS = const(200)

HB_PERIOD = const(10)
NON_BLK_SOCK = const(0)
MIN_SOCK_TO = const(1)  # 1 second
MAX_SOCK_TO = const(5)  # 5 seconds, must be < HB_PERIOD
WDT_TO = const(10000)  # 10 seconds
RECONNECT_DELAY = const(1)  # 1 second
TASK_PERIOD_RES = const(50)  # 50 ms
IDLE_TIME_MS = const(5)  # 5 ms

RE_TX_DELAY = const(2)
MAX_TX_RETRIES = const(3)

MAX_VIRTUAL_PINS = const(32)

DISCONNECTED = 0
CONNECTING = 1
AUTHENTICATING = 2
AUTHENTICATED = 3

EAGAIN = const(11)


def sleep_from_until(start, delay):
    while abs(time.ticks_diff(start, time.ticks_ms())) < delay:
        machine.idle()
    return start + delay


class HwPin:

    _TimerMap = {
        9: (10),
        10: (12),
        11: (13),
        24: (14),
        25: (25),
    }

    _ADCMap = {
        2: (36),  # ESP32:
        3: (39),  # remapping pins to available ADC
        4: (34),
        5: (35),
    }
    _HBPin = 2

    def __init__(self, pin_num, mode, pull):
        self._mode = mode
        self._pull = pull
        self._function = ""
        self._pin = None
        self._apin = None
        self._pwm = None
        pin_num = int(pin_num)
        self._name = pin_num

    def _config(self, duty_cycle=0):
        if self._function == "dig":
            _mode = machine.Pin.OUT if self._mode == "out" else machine.Pin.IN
            if self._pull == "pu":
                _pull = machine.Pin.PULL_UP
            elif self._pull == "pd":
                _pull = machine.Pin.PULL_DOWN
            else:
                _pull = None
            self._pin = machine.Pin(self._name, mode=_mode, pull=_pull)
        elif self._function == "ana":
            self._apin = machine.ADC(machine.Pin(self._ADCMap[self._name]))
        else:
            self._pwm = machine.PWM(machine.Pin(self._TimerMap[self._name]), freq=20000)

    def digital_read(self):
        if self._function != "dig":
            self._function = "dig"
            self._config()
        return self._pin()

    def digital_write(self, value):
        if self._function != "dig":
            self._function = "dig"
            self._config()
        self._pin(value)

    def analog_read(self):
        if self._function != "ana":
            self._function = "ana"
            self._config()
            return self._apin.read()

    def analog_write(self, value):
        if self._function != "pwm":
            self._function = "pwm"
            self._config(value)     # ToDo: recalculate duty
        else:
            self._pwm.duty(value)   # ToDo: recalculate duty


class VrPin:
    def __init__(self, read=None, write=None):
        self.read = read
        self.write = write


class Terminal:
    def __init__(self, blynk, pin):
        self._blynk = blynk
        self._pin = pin

    def write(self, data):
        self._blynk.virtual_write(self._pin, data)

    def read(self, size):
        return ""

    def virtual_read(self):
        pass

    def virtual_write(self, value):
        try:
            out = eval(value)
            if out != None:
                print(repr(out))
        except:
            try:
                exec(value)
            except Exception as e:
                print("Exception:\n  " + repr(e))


class Blynk:
    def __init__(
        self,
        token,
        server="blynk-cloud.com",
        port=None,
        connect=True,
        wdt=True,
        ssl=False,
    ):
        self._wdt = None
        self._vr_pins = {}
        self._do_connect = False
        self._on_connect = None
        self._task = None
        self._task_period = 0
        self._token = token
        if isinstance(self._token, str):
            self._token = bytes(token, "ascii")
        self._server = server
        if port is None:
            if ssl:
                port = 8441
            else:
                port = 8442
        self._port = port
        self._do_connect = connect
        self._wdt = wdt
        self._ssl = ssl
        self.state = DISCONNECTED

    def _format_msg(self, msg_type, *args):
        data = bytes("\0".join(map(str, args)), "ascii")
        return struct.pack(HDR_FMT, msg_type, self._new_msg_id(), len(data)) + data

    def _handle_hw(self, data):
        params = list(map(lambda x: x.decode("ascii"), data.split(b"\0")))
        cmd = params.pop(0)
        print(params)
        if cmd == "info":
            pass
        elif cmd == "pm":
            pairs = zip(params[0::2], params[1::2])
            for (pin, mode) in pairs:
                pin = int(pin)
                if mode != "in" and mode != "out" and mode != "pu" and mode != "pd":
                    raise ValueError("Unknown pin %d mode: %s" % (pin, mode))
                self._hw_pins[pin] = HwPin(pin, mode, mode)
            self._pins_configured = True
        elif cmd == "vw":
            pin = int(params.pop(0))
            if pin in self._vr_pins and self._vr_pins[pin].write:
                for param in params:
                    self._vr_pins[pin].write(param)
            else:
                print("Warning: Virtual write to unregistered pin %d" % pin)
        elif cmd == "vr":
            pin = int(params.pop(0))
            if pin in self._vr_pins and self._vr_pins[pin].read:
                self._vr_pins[pin].read()
            else:
                print("Warning: Virtual read from unregistered pin %d" % pin)
        elif self._pins_configured:
            if cmd == "dw":
                pin = int(params.pop(0))
                val = int(params.pop(0))
                self._hw_pins[pin].digital_write(val)
            elif cmd == "aw":
                pin = int(params.pop(0))
                val = int(params.pop(0))
                self._hw_pins[pin].analog_write(val)
            elif cmd == "dr":
                pin = int(params.pop(0))
                val = self._hw_pins[pin].digital_read()
                self._send(self._format_msg(MSG_HW, "dw", pin, val))
            elif cmd == "ar":
                pin = int(params.pop(0))
                val = self._hw_pins[pin].analog_read()
                self._send(self._format_msg(MSG_HW, "aw", pin, val))
            else:
                raise ValueError("Unknown message cmd: %s" % cmd)

    def _new_msg_id(self):
        self._msg_id += 1
        if self._msg_id > 0xFFFF:
            self._msg_id = 1
        return self._msg_id

    def _settimeout(self, timeout):
        if timeout != self._timeout:
            self._timeout = timeout
            self.conn.settimeout(timeout)

    def _recv(self, length, timeout=0):
        self._settimeout(timeout)
        try:
            self._rx_data += self.conn.recv(length)
        except OSError as exc:
            if exc.args[0] == errno.ETIMEDOUT:
                return b""
            elif exc.args[0] == errno.EAGAIN:
                 return b""
            else:
                raise
        #except socket.timeout:
        #    return b""
        #except socket.error as e:
        #    if e.args[0] == EAGAIN:
        #        return b""
        #    else:
        #        raise

        if len(self._rx_data) >= length:
            data = self._rx_data[:length]
            self._rx_data = self._rx_data[length:]
            return data
        else:
            return b""

    def _send(self, data, send_anyway=False):
        if self._tx_count < MAX_MSG_PER_SEC or send_anyway:
            retries = 0
            while retries <= MAX_TX_RETRIES:
                try:
                    self.conn.send(data)
                    self._tx_count += 1
                    break
                except socket.error as er:
                    if er.args[0] != EAGAIN:
                        raise
                    else:
                        time.sleep_ms(RE_TX_DELAY)
                        retries += 1

    def _close(self, emsg=None):
        self.conn.close()
        self.state = DISCONNECTED
        time.sleep(RECONNECT_DELAY)
        if emsg:
            print("Error: %s, connection closed" % emsg)

    def _server_alive(self):
        c_time = int(time.time())
        if self._m_time != c_time:
            self._m_time = c_time
            self._tx_count = 0
            if self._wdt:
                self._wdt.feed()
            if self._last_hb_id != 0 and c_time - self._hb_time >= MAX_SOCK_TO:
                return False
            if c_time - self._hb_time >= HB_PERIOD and self.state == AUTHENTICATED:
                self._hb_time = c_time
                self._last_hb_id = self._new_msg_id()
                self._send(struct.pack(HDR_FMT, MSG_PING, self._last_hb_id, 0), True)
        return True

    def _run_task(self):
        if self._task:
            c_millis = time.ticks_ms()
            if c_millis - self._task_millis >= self._task_period:
                self._task_millis += self._task_period
                self._task()

    def repl(self, pin):
        repl = Terminal(self, pin)
        self.add_virtual_pin(pin, repl.virtual_read, repl.virtual_write)
        return repl

    def notify(self, msg):
        if self.state == AUTHENTICATED:
            self._send(self._format_msg(MSG_NOTIFY, msg))

    def tweet(self, msg):
        if self.state == AUTHENTICATED:
            self._send(self._format_msg(MSG_TWEET, msg))

    def email(self, to, subject, body):
        if self.state == AUTHENTICATED:
            self._send(self._format_msg(MSG_EMAIL, to, subject, body))

    def virtual_write(self, pin, val):
        if self.state == AUTHENTICATED:
            self._send(self._format_msg(MSG_HW, "vw", pin, val))

    def sync_all(self):
        if self.state == AUTHENTICATED:
            self._send(self._format_msg(MSG_HW_SYNC))

    def sync_virtual(self, pin):
        if self.state == AUTHENTICATED:
            self._send(self._format_msg(MSG_HW_SYNC, "vr", pin))

    def add_virtual_pin(self, pin, read=None, write=None):
        if isinstance(pin, int) and pin in range(0, MAX_VIRTUAL_PINS):
            self._vr_pins[pin] = VrPin(read, write)
        else:
            raise ValueError(
                "the pin must be an integer between 0 and %d" % (MAX_VIRTUAL_PINS - 1)
            )

    def on_connect(self, func):
        self._on_connect = func

    def set_user_task(self, task, ms_period):
        if ms_period % TASK_PERIOD_RES != 0:
            raise ValueError(
                "the user task period must be a multiple of %d ms" % TASK_PERIOD_RES
            )
        self._task = task
        self._task_period = ms_period

    def connect(self):
        self._do_connect = True

    def disconnect(self):
        self._do_connect = False

    def run(self):
        self._start_time = time.ticks_ms()
        self._task_millis = self._start_time
        self._hw_pins = {}
        self._rx_data = b""
        self._msg_id = 1
        self._pins_configured = False
        self._timeout = None
        self._tx_count = 0
        self._m_time = 0
        self.state = DISCONNECTED

        if self._wdt:
            self._wdt = machine.WDT()

        while True:
            while self.state != AUTHENTICATED:
                self._run_task()
                if self._wdt:
                    self._wdt.feed()
                if self._do_connect:
                    try:
                        self.state = CONNECTING
                        if self._ssl:
                            import ssl

                            print(
                                "SSL: Connecting to %s:%d" % (self._server, self._port)
                            )
                            ss = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            self.conn = ssl.wrap_socket(ss)
                        else:
                            print(
                                "TCP: Connecting to %s:%d" % (self._server, self._port)
                            )
                            self.conn = socket.socket()
                        self.conn.connect(
                            socket.getaddrinfo(self._server, self._port)[0][4]
                        )
                    except:
                        self._close("connection with the Blynk servers failed")
                        continue

                    self.state = AUTHENTICATING
                    hdr = struct.pack(
                        HDR_FMT, MSG_LOGIN, self._new_msg_id(), len(self._token)
                    )
                    print("Blynk connection successful, authenticating...")
                    self._send(hdr + self._token, True)
                    data = self._recv(HDR_LEN, timeout=MAX_SOCK_TO)
                    if not data:
                        self._close("Blynk authentication timed out")
                        continue

                    msg_type, msg_id, status = struct.unpack(HDR_FMT, data)
                    if status != STA_SUCCESS or msg_id == 0:
                        self._close("Blynk authentication failed")
                        continue

                    self.state = AUTHENTICATED
                    self._send(
                        self._format_msg(
                            MSG_HW_INFO,
                            "h-beat",
                            HB_PERIOD,
                            "dev",
                            "WiPy",
                            "cpu",
                            "CC3200",
                        )
                    )
                    print("Access granted, happy Blynking!")
                    if self._on_connect:
                        self._on_connect()
                else:
                    self._start_time = sleep_from_until(
                        self._start_time, TASK_PERIOD_RES
                    )

            self._hb_time = 0
            self._last_hb_id = 0
            self._tx_count = 0
            while self._do_connect:
                #print('do_connect')
                data = self._recv(HDR_LEN, NON_BLK_SOCK)
                if data:
                    msg_type, msg_id, msg_len = struct.unpack(HDR_FMT, data)
                    if msg_id == 0:
                        self._close("invalid msg id %d" % msg_id)
                        break
                    if msg_type == MSG_RSP:
                        if msg_id == self._last_hb_id:
                            self._last_hb_id = 0
                    elif msg_type == MSG_PING:
                        self._send(
                            struct.pack(HDR_FMT, MSG_RSP, msg_id, STA_SUCCESS), True
                        )
                    elif msg_type == MSG_HW or msg_type == MSG_BRIDGE:
                        data = self._recv(msg_len, MIN_SOCK_TO)
                        if data:
                            self._handle_hw(data)
                    else:
                        self._close("unknown message type %d" % msg_type)
                        break
                else:
                    self._start_time = sleep_from_until(self._start_time, IDLE_TIME_MS)
                if not self._server_alive():
                    self._close("Blynk server is offline")
                    break
                self._run_task()

            if not self._do_connect:
                self._close()
                print("Blynk disconnection requested by the user")
            gc.collect()
