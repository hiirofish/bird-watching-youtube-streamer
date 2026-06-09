from smbus2 import SMBus, i2c_msg
import time
import os
import zmq

zmq_ctx = zmq.Context()
zmq_sock = zmq_ctx.socket(zmq.REQ)
zmq_sock.setsockopt(zmq.REQ_RELAXED, 1)
zmq_sock.setsockopt(zmq.REQ_CORRELATE, 1)
zmq_sock.setsockopt(zmq.RCVTIMEO, 3000)
zmq_sock.setsockopt(zmq.SNDTIMEO, 3000)
zmq_sock.connect("tcp://127.0.0.1:5555")
BUS = 1  # ソフトI2C
bus = SMBus(BUS)

# ===== SHT30 (0x44) =====
def read_sht30():
    bus.i2c_rdwr(i2c_msg.write(0x44, [0x2C, 0x06]))
    time.sleep(0.5)
    read = i2c_msg.read(0x44, 6)
    bus.i2c_rdwr(read)
    data = list(read)
    if data[0] == 0xFF and data[1] == 0xFF:
        return None, None
    temp = -45 + (175 * (data[0]<<8|data[1]) / 65535.0)
    humi = 100 * (data[3]<<8|data[4]) / 65535.0
    return temp, humi

# ===== BMP180 (0x77) =====
_, *_ = (None,)
raw = bus.read_i2c_block_data(0x77, 0xAA, 22)
def s(v): return v - 65536 if v > 32767 else v
n = ['AC1','AC2','AC3','AC4','AC5','AC6','B1','B2','MB','MC','MD']
sg = [1,1,1,0,0,0,1,1,1,1,1]
c = {}
for i in range(11):
    v = raw[i*2]<<8|raw[i*2+1]
    c[n[i]] = s(v) if sg[i] else v

def read_bmp180():
    bus.write_byte_data(0x77, 0xF4, 0x2E)
    time.sleep(0.005)
    rt = bus.read_i2c_block_data(0x77, 0xF6, 2)
    UT = rt[0]<<8|rt[1]

    bus.write_byte_data(0x77, 0xF4, 0xF4)
    time.sleep(0.026)
    rp = bus.read_i2c_block_data(0x77, 0xF6, 3)
    UP = ((rp[0]<<16)+(rp[1]<<8)+rp[2])>>5

    X1=(UT-c['AC6'])*c['AC5']/32768
    X2=c['MC']*2048/(X1+c['MD'])
    B5=X1+X2
    temp=(B5+8)/16/10.0
    B6=B5-4000
    X1=(c['B2']*(B6*B6/4096))/2048
    X2=c['AC2']*B6/2048
    X3=X1+X2
    B3=(((c['AC1']*4+int(X3))<<3)+2)/4
    X1=c['AC3']*B6/8192
    X2=(c['B1']*(B6*B6/4096))/65536
    X3=((X1+X2)+2)/4
    B4=c['AC4']*(X3+32768)/32768
    B7=(UP-B3)*6250
    p=(B7*2)/B4 if B7<0x80000000 else (B7/B4)*2
    X1=(p/256)**2*3038/65536
    X2=-7357*p/65536
    p=p+(X1+X2+3791)/16
    return temp, p/100

try:
    print("気象観測開始 (Ctrl+Cで終了)\n")
    while True:
        temp, humi = read_sht30()
        bmp_temp, pressure = read_bmp180()
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        
        # Build the on-screen string: integers only, no T:/H:/P: labels.
        # Separator is a FULL-WIDTH space (U+3000): ffmpeg's zmq command parser
        # tokenizes on ASCII spaces, so ordinary spaces get merged/lost. A
        # full-width space is not ASCII whitespace, so it survives intact and
        # also gives a clear visual gap.
        if temp is not None:
            output_str = f"{temp:.0f}\u00b0C\u3000{humi:.0f}%\u3000{pressure:.0f}hPa"
            print(f"{ts}  {output_str}")
        else:
            output_str = f"--\u00b0C\u3000--%\u3000{pressure:.0f}hPa"
            print(f"{ts}  SHT30 error  P:{pressure:.0f}hPa")

        # Send to FFmpeg via ZMQ. The new format has no colons; only escape
        # backslash and single-quote so the quoted command stays well-formed.
        # ('%' is safe because the drawtext filter is created with expansion=none.)
        try:
            escaped = output_str.replace("\\", "\\\\").replace("'", "\\'")
            zmq_sock.send_string(f"drawtext@weather reinit text='{escaped}'")
            reply = zmq_sock.recv_string(0)
        except Exception as e:
            print(f"ZMQ send error: {e}")

        time.sleep(10)
except KeyboardInterrupt:
    print("\n終了")
finally:
    bus.close()
    zmq_sock.close()
    zmq_ctx.term()