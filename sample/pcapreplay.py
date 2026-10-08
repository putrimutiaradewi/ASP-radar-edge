#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import time
import re

from progressbar import *
import getopt
import gzip
import hashlib
import json
import random

import logging
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)

import socket
from scapy.all import *
from scapy.utils import rdpcap

import threading
# import subprocess

# global parameter
debug = False
verbose = False
quite = False
listen = False      # listen is server, not listen is client
beast_server = False    # serve Beast TCP stream from pcap (for aeroswarm integration)
asterix_replay = False  # replay ASTERIX UDP packets to a destination (for aeroswarm integration)
target = ""
port = 6324
interface = ""
pcap_file = ""
speed = 1
quick = False

# protocol = ""

def info(str):
    global quite
    if not quite:
        print("[INFO] " + str)

def error(str):
    print("[ERROR] " + str)

def exit_error(str):
    print("[ERROR] " + str)
    sys.exit(1)

def debugger(str):
    global quite
    global debug
    if debug and not quite:
        print("[DEBUG] " + str)

def verboser(str):
    global quite
    global verbose
    if verbose and not quite:
        print(str)

def get_local_interface():
    interfaces = get_if_list()
    iface_num = len(interfaces)
    if iface_num == 0:
        exit_error("No avaliable interface!Exit!")
    for i in range(0, iface_num):
        if interfaces[i] != "lo":
            return interfaces[i]
        else:
            continue

def validate_ip(ip):
    compile_ip = re.compile(r'^(1\d{2}|2[0-4]\d|25[0-5]|[1-9]\d|[1-9])\.(1\d{2}|2[0-4]\d|25[0-5]|[1-9]\d|\d)\.(1\d{2}|2[0-4]\d|25[0-5]|[1-9]\d|\d)\.(1\d{2}|2[0-4]\d|25[0-5]|[1-9]\d|\d)$')
    if compile_ip.match(ip):
        return True 
    else:  
        return False    
def validate_port(port):
    if port < 0 or port > 65535:
        return False
    else:
        return True

# Block calculation
def get_file_md5(file_name):
    md5 = hashlib.md5()
    with open(file_name, 'rb') as file_obj:
        while True:
            data = file_obj.read(4096)
            if not data:
                break
            md5.update(data)
    return md5.hexdigest()

def zip_file(file_name):
    ziped_file = file_name + '.gz'
    # Zip pcap
    debugger("Zip %s into %s" %(pcap_file, ziped_file))
    zip_obj = gzip.GzipFile(filename = file_name, mode = "wb+", compresslevel = 9, fileobj = open(ziped_file, 'wb'))
    zip_obj.write(open(file_name, 'rb').read())
    zip_obj.close()
    return ziped_file

def unzip_file(file_name):
    pcap_file = os.path.splitext(file_name)[0]
    debugger("Unzip into %s" %pcap_file)
    zip_obj = gzip.GzipFile(mode = "rb", fileobj = open(file_name, "rb"))
    open(pcap_file, "wb+").write(zip_obj.read())
    return pcap_file

def send_file(client):
    global pcap_file

    ziped_file = zip_file(pcap_file)
    # Convert the header to a string (json.dumps), and then pack the length of the string.
    # Send header length, then send header content, and finally replay content.
    # Header contents include file name, file information, header

    # file_index = 0
    debugger("Get file bytes size")
    filesize_bytes = os.path.getsize(ziped_file)  
    debugger("Get file md5")
    file_md5 = get_file_md5(ziped_file)
    debugger("Create header info")
    dirc = {
        # 'fileindex': file_index,
        'filename': ziped_file,
        'filesize_bytes': filesize_bytes,
        'md5': file_md5
    }
    # Convert dic to string
    debugger("Convert file dic to string")
    head_info = json.dumps(dirc)    
    # print(head_info)
    head_info_len = struct.pack('i', len(head_info))
    # print(head_info_len)  

    # Send the length of head_info
    client.sendall(head_info_len)
    client.sendall(head_info.encode('utf-8'))

    # Send file
    with open(ziped_file, 'rb') as fd:
        while True:
            chunk = fd.read(8192)
            if not chunk:
                break
            client.sendall(chunk)
    info("Send %s" %ziped_file)

def recvall(sock, n):
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed before receiving all data")
        data += chunk
    return data

def receive_file(server):

    buffer_size = 1024

    info("Waiting for header length...")
    head_struct = recvall(server, 4)
    info("Got header length: %s" % str(head_struct))
    head_len = struct.unpack('i', head_struct)[0]
    info("Waiting for header data (%d bytes)..." % head_len)
    data = recvall(server, head_len)
    info("Got header data")
    head_dir = json.loads(data.decode('utf-8'))
    filesize_b = head_dir['filesize_bytes']
    filename = head_dir['filename']
    md5 = head_dir['md5']
    info("Expecting file: %s (%d bytes)" % (filename, filesize_b))
    recv_len = 0

    info("Opening file for write...")
    with open(filename, 'wb+') as fd:
        info("Entering recv loop")
        while recv_len < filesize_b:
            chunk_size = min(buffer_size, filesize_b - recv_len)
            info("Calling recv(%d), have %d/%d bytes" % (chunk_size, recv_len, filesize_b))
            chunk = server.recv(chunk_size)
            if not chunk:
                raise ConnectionError("Connection closed during file receive after %d/%d bytes" % (recv_len, filesize_b))
            fd.write(chunk)
            recv_len += len(chunk)

    return filename, md5

# sync pcap file between client and server
def sync_file(host):
    # listen is server, not listen is client
    global listen
    global pcap_file

    if not listen:
        try:
            info("Try sending pcap file to server")
            send_file(host)
            info("Send file complete")
            # client.send("200 send file success".encode('utf-8'))
        except:
            host.send("SDFA".encode('utf-8'))
            exit_error("SDFA: send file failed")
    # server unzip file module
    else:
        try:

            info("Try receiving pcap file from client")
            ziped_file, head_md5 = receive_file(host)

            debugger("Check if file md5 is accurate")
            file_md5 = get_file_md5(ziped_file)
            if file_md5 == head_md5:
                info("Valid file md5")
                host.send("RCSC".encode('utf-8'))
                info("RCSC: receive file success")
            else:
                exit_error("Invalid file md5! Failed to receive file")
                host.send("RCFA".encode('utf-8'))
                exit_error("RCFA: receive file error")
            
            debugger("Unzip pcap file")
            pcap_file = unzip_file(ziped_file)
            if os.path.exists(ziped_file):
                os.remove(ziped_file)
        except Exception as e:
            import traceback
            with open("server_error.log", "w") as f:
                traceback.print_exc(file=f)
            host.send("RCFA".encode('utf-8'))
            exit_error("RCFA: receive file error: %s" % e)

# Deprecated function
#
# def get_adjacent_index(packet_num, packet_index, i):
#     if i == 0:
#         last_index = -1
#     else:
#         last_index = packet_index[i-1]

#     packet_num = len(packet_index)   
    
#     if i == packet_num - 1:
#         next_index = -1
#     else:
#         next_index = packet_index[i+1]

#     return last_index, next_index        

# def is_continue(num1, num2):
#     if num1 + 1 == num2:
#         return True
#     elif num2 + 1 == num1:
#         return True
#     else:
#         return False

# def send_packet(host):
#     global pcap_file
#     debugger("Load pcap")
    
#     packets = rdpcap(pcap_file)
#     packet_index = get_packet_index(packets)
    
#     total = len(packets)
#     packet_num = len(packet_index)
#     t0 = time.time()
#     info("Start send packet")
#     for i in range(0, packet_num):

#         current_index = packet_index[i]
#         last_index, next_index = get_adjacent_index(packet_num, packet_index, i)

#         if is_continue(current_index, last_index):
#             debugger("last:" + str(last_index)+" =>" + "current:" + str(current_index) + ", continue")
#             if i != 0:
#                 delta = packets[current_index].time - packets[last_index].time
#                 time.sleep(delta)
#             # scapy send
#             timestamp = round((time.time() - t0), 6) 
#             sendp(packets[current_index], verbose = False, return_packets = True)
#             # verboser("I:" + str(current_index) + "T:" + str(timestamp) + " ==>" + " " + packets[current_index].summary())

#         elif not is_continue(current_index, last_index) or (last_index == -1 and current_index != total -1 ):
#             debugger("last:" + str(last_index) + " =>" + "current:" + str(current_index) + ", not continue")
#             while True:
#                 index = host.recv(4)
#                 if len(index):
#                     timestamp = round((time.time() - t0), 6) 
#                     remote_index = struct.unpack('i', index)[0]
#                     debugger("last remote index: " + str(remote_index))
#                     verboser("I:" + str(remote_index) + "T:" + str(timestamp) + " <==" + " " + packets[remote_index].summary())
#                     if remote_index == current_index - 1:
#                         delta = packets[current_index].time - packets[remote_index].time
#                         time.sleep(delta)
#                         # scapy send
#                         timestamp = round((time.time() - t0), 6) 
#                         sendp(packets[current_index], verbose = False, return_packets = True)
#                         # verboser("I:" + str(current_index) + "T:" + str(timestamp) +  " ==>" + " " + packets[current_index].summary())
#                         break
        
#         if not is_continue(next_index, current_index):
#             debugger("current:" + str(current_index) + " =>" + "next:" + str(next_index) + ", not continue")
#             index = struct.pack('i', current_index)
#             debugger("Send current index:" + str(current_index))
#             host.send(index)

#     info("Send all packets finished, connection will be closed")
#     time.sleep(1)
#     info("Exit")
#     host.close()
#     info("Server wiil continue listening on port %d" %port)
#     sys.exit(0)

# def set_inner_mode(packets):

def get_packet_index(packets):
    global listen

    packet_num = len(packets)
    addr = []
    packet_index = []

    for i in range(0, packet_num):
        if IP not in packets[i] or TCP not in packets[i]:
            continue
        try:
            src_addr = packets[i][IP].src + ":" + str(packets[i][TCP].sport)
            dst_addr = packets[i][IP].dst + ":" + str(packets[i][TCP].dport)
            # tcp stream SYN
            if packets[i][TCP].flags == 0x02:
                if not listen:
                    # Client is src
                    addr.append(src_addr)
                else:
                    # Server is dst 
                    addr.append(dst_addr)
            if src_addr in addr:
                packet_index.append(i)

            # # tcp stream FIN and ACK
            # if packets[i-1][TCP].flags == 0x11 and packets[i][TCP].flags == 0x10:
            #     if not listen:
            #         # Delete client in addr list
            #         del addr[addr.index(dst_addr)]
            #         # addr[addr.index(dst_addr)] = ''
            #     else:
            #         # Delete server in addr list
            #         del addr[addr.index(dst_addr)]
            #         # addr[addr.index(src_addr)] = ''
        except:
            pass

    return packet_index    

def send_packet(host, packets, index, t0):
    timestamp = round((time.time() - t0), 6)   
    sendp(packets[index], iface = interface, verbose = False)
    host.send(struct.pack('i', index))
    verboser("I:" + str(index+1) + " T:" + str(timestamp) +  " ==>" + " " + packets[index].summary())

def recv_packet(host, packets, t0):
    while True:
        recv_index = host.recv(4)
        if len(recv_index):
            timestamp = round((time.time() - t0), 6)    
            remote_index = struct.unpack('i', recv_index)[0]
            verboser("I:" + str(remote_index+1) + " T:" + str(timestamp) + " <==" + " " + packets[remote_index].summary())
            break

def replay_packet(host):
    global pcap_file
    global speed
    global quick
    # global module

    debugger("Load pcap")
    packets = rdpcap(pcap_file)

    # tamper = module

    own_packet_index = get_packet_index(packets)
    
    packet_num = len(packets)

    info("Start replay packet")

    t0 = time.time()
    for i in range (0, packet_num):
        if i in own_packet_index:
            if (i != 0) and not quick:
                delta = (packets[i].time - packets[i-1].time)/speed
                time.sleep(delta)
            send_packet(host, packets, i, t0)
        else:
            recv_packet(host, packets, t0)
            
    info("Send all packets finished, connection will be closed")
    if listen:
        info("Server wiil continue listening on port %d" %port)
    else:
        info("Exit")
        host.close()
    sys.exit(0)

def run_as_asterix_replay():
    global pcap_file, speed, quick, target, port

    KNOWN_CATS = {21, 48, 23, 34, 10, 1, 2, 8, 11, 19, 20, 62, 63, 247}

    info("Loading pcap: %s" % pcap_file)
    packets = rdpcap(pcap_file)

    asterix_pkts = []
    for pkt in packets:
        if UDP not in pkt or Raw not in pkt:
            continue
        raw = bytes(pkt[Raw])
        if len(raw) < 3:
            continue
        cat = raw[0]
        length = int.from_bytes(raw[1:3], 'big')
        if length == len(raw) and cat in KNOWN_CATS:
            asterix_pkts.append((float(pkt.time), raw))

    if not asterix_pkts:
        exit_error("No ASTERIX UDP packets found in pcap. Check that the file contains ASTERIX traffic.")

    # Count per category
    from collections import Counter
    cat_counts = Counter(p[0] for _, p in asterix_pkts)
    info("Found %d ASTERIX packets: %s" % (len(asterix_pkts), dict(cat_counts)))

    dest_host = target if target else "127.0.0.1"
    dest_port = port

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    info("Replaying to %s:%d  (speed=%.1fx, quick=%s)" % (dest_host, dest_port, speed, quick))

    t0 = time.time()
    first_ts = asterix_pkts[0][0]

    for ts, payload in asterix_pkts:
        if not quick:
            elapsed_pcap = (ts - first_ts) / speed
            elapsed_real = time.time() - t0
            wait = elapsed_pcap - elapsed_real
            if wait > 0:
                time.sleep(wait)
        sock.sendto(payload, (dest_host, dest_port))
        debugger("Sent %d bytes ASTERIX CAT%d" % (len(payload), payload[0]))

    info("Replay complete — sent %d ASTERIX packets" % len(asterix_pkts))
    sock.close()


def extract_beast_stream(packets):
    """Extract server-side TCP payload bytes from pcap in timestamp order.
    Server is identified by SYN+ACK (flags=0x12).
    Returns list of (timestamp_float, bytes) from the dump1090 / Beast sender side.
    """
    server_addrs = set()

    for pkt in packets:
        if IP not in pkt or TCP not in pkt:
            continue
        if pkt[TCP].flags == 0x12:  # SYN+ACK => this src is the server
            server_addrs.add((pkt[IP].src, pkt[TCP].sport))

    if not server_addrs:
        # Fallback: treat the dst of the first SYN as server
        for pkt in packets:
            if IP not in pkt or TCP not in pkt:
                continue
            if pkt[TCP].flags == 0x02:  # SYN
                server_addrs.add((pkt[IP].dst, pkt[TCP].dport))
                break

    stream = []
    for pkt in packets:
        if IP not in pkt or TCP not in pkt:
            continue
        key = (pkt[IP].src, pkt[TCP].sport)
        payload = bytes(pkt[TCP].payload)
        if payload and key in server_addrs:
            stream.append((float(pkt.time), payload))

    return stream


def beast_serve_client(conn, stream_data):
    global speed, quick

    try:
        t0 = time.time()
        first_ts = stream_data[0][0]

        for ts, payload in stream_data:
            if not quick:
                elapsed_pcap = (ts - first_ts) / speed
                elapsed_real = time.time() - t0
                wait = elapsed_pcap - elapsed_real
                if wait > 0:
                    time.sleep(wait)
            conn.sendall(payload)

        info("Finished streaming Beast data to client")
    except (BrokenPipeError, ConnectionResetError):
        info("Client disconnected")
    finally:
        conn.close()


def run_as_beast_server():
    global pcap_file, port, target

    info("Loading pcap: %s" % pcap_file)
    packets = rdpcap(pcap_file)

    stream_data = extract_beast_stream(packets)
    if not stream_data:
        exit_error("No server-side TCP payload found in pcap. Is this a Beast TCP capture?")

    info("Extracted %d Beast data chunks from pcap" % len(stream_data))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_host = target if target else "0.0.0.0"
    srv.bind((listen_host, port))
    srv.listen(5)
    info("Beast server ready — aeroswarm TcpBeastReceiver connect to %s:%d" % (listen_host, port))

    while True:
        conn, client_addr = srv.accept()
        info("Client connected: %s:%d" % (client_addr[0], client_addr[1]))
        t = threading.Thread(target=beast_serve_client, args=(conn, stream_data))
        t.daemon = True
        t.start()


def run_as_server():
    global target
    global port

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((target, port))
    server.listen(100)
    info("Listening on %s:%d" %(target, port))

    while True:

        conn, client_addr = server.accept()
        info("Accept connection from %s:%d" %(client_addr[0], client_addr[1]))
        conn_thread = threading.Thread(target = conn_handler, args = (conn,))
        conn_thread.start()
        
def conn_handler(conn):
    global pcap_file

    conn.send("CNAC".encode('utf-8'))
    debugger("CNAC: connection accepted")
    while True:

        if not conn:
            info("%s:%d has disconnected" %(addr[0], addr[1]))
            break

        request = conn.recv(4).decode('utf-8')
        if request == "":
            exit_error("connection closed by peer")
            conn.close()
        elif request == "CNES":
            debugger("CLIENT CE => connection established")
            sync_file(conn)
            replay_packet(conn)
        elif request == "CDFA":
            exit_error("CLIENT CDFA => send file failed")
    

def run_as_client():
    global target
    global port
    global pcap_file

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    try:
        # connet to our target host
        info("Try connecting to %s:%d" %(target, port))
        client.connect((target, port))

        while True:
            response = client.recv(4).decode('utf-8')
            if response == "":
                error("Connection closed by peer")
                client.close()
            elif response == "CNAC":
                debugger("SERVER CNAC => connection accepted")
                info("Success connecting to server, start sync pcap file")
                client.send("CNES".encode('utf-8'))
                debugger("CNES connected established")
                # send file to server
                sync_file(client)
            elif response == "RCSC":
                debugger("SERVER RCSC => receive file success")
                if os.path.exists(pcap_file+'.gz'):
                    os.remove(pcap_file+'.gz')
                replay_packet(client)
            elif response == "RCFA":
                exit_error("SERVER RCFA => receive file failed")

    except socket.error as exc:
        # just catch generic errors
        error("Exception! Exiting.")
        error("Caught exception socket.error: %s" %exc)

        # teardown the connection
        client.close()

def usage():
    print("PcapReplayer v0.1 by Lithium")
    print("Usage:")
    print("SERVER: pcapreplay.py -i [interface] --listen -t [listen_target] -p [port]")
    print("CLIENT: pcapreplay.py -i [interface] -t [target] -p [port] -f [pcapfile]")
    print("-i --interface             - CLIENT Client to server traffic output interface")
    print(                             "SERVER Server to client traffic output interface")
    print(                             "Default use the first avaliable iface")
    print("-f --file                  - CLIENT upon receiving connection upload a file and write to [target]")
    print("-l --listen                - SERVER listen on [host]:[port] for incoming connections")
    print("-t --target                - CLIENT connect to target host")
    print(                             "SERVER listening on this host, default on 0.0.0.0")
    print("-p --port                  - CLIENT connect to target port")
    print(                             "SERVER listen on this port")
    print(                             "Default use port 6324")
    print("-s --speed                 - ajust the speed of replay, -s 4 means replay by 4 times the speed")
    print("-Q --quick                 - All packets will be sent finished immediately, must be selected by both ends at the same time")
    print("-v --verbose               - Print decoded packets via tcpdump to STDOUT")
    print("-d --debug                 - Initiate with debugging mode")
    print("-q --quite                 - Unimportant infomation will be filterd")
    print("-h --help                  - Extended usage information passed thru pager")
    print("--beast-server             - Serve Beast TCP stream from pcap so aeroswarm TcpBeastReceiver can connect")
    print("--asterix-replay           - Replay ASTERIX UDP packets from pcap to -t host -p port (default: 127.0.0.1:41234)")
    print("Run as SERVER: ")
    print("pcapreplay.py -i eth0 --listen -p 6324")
    print("Run as CLIENT:")
    print("pcapreplay.py -i eth0 -f [pcap_file] -t 192.168.1.24 -p 6324")
    print("Run as Beast Server (for aeroswarm):")
    print("pcapreplay.py --beast-server -f [pcap_file] -p 30005")
    print("Run as ASTERIX Replay (for aeroswarm):")
    print("pcapreplay.py --asterix-replay -f [pcap_file] -t 127.0.0.1 -p 41234")
    sys.exit(0)

def main():
    global listen
    global beast_server
    global asterix_replay
    global target
    global port
    global pcap_file
    global interface
    global debug
    global verbose
    global speed
    global quite
    global quick

    if not len(sys.argv[1:]):
        usage()
    # read the commandlien options
    try:
        opts, args = getopt.getopt(sys.argv[1:], "hldvqt:p:i:f:s:Q", ["help", "listen", "beast-server", "asterix-replay", "debug", "verbose", "quite", "target", "port", "interface", "pacpfile", "speed", "quick"])
        for o,a in opts:
            if o in ("-h", "--help"):
                usage()
            elif o in ("-l", "--listen"):
                listen = True
            elif o in ("--beast-server",):
                beast_server = True
            elif o in ("--asterix-replay",):
                asterix_replay = True
            elif o in ("-t", "--target"):
                target = a
            elif o in ("-p", "--port"):
                port = int(a)
            elif o in ("-f", "--file"):
                pcap_file = a
            elif o in ("-i", "--interface"):
                interface = a
            elif o in ("-d", "--debug"):
                debug = True
            elif o in ("-v", "--verbose"):
                verbose = True
            elif o in ("-q", "--quite"):
                quite = True
            elif o in ("-s", "--speed"):
                speed = int(a)
            elif o in ("-Q", "--quick"):
                quick = True
            else:
                assert False, "Unhandled Option"
    except getopt.GetoptError as err:
        print(str(err))
        usage()

    # ASTERIX replay mode: send UDP ASTERIX packets to destination
    if asterix_replay:
        if not len(pcap_file):
            exit_error("--asterix-replay requires -f [pcap_file]")
        if not os.path.exists(pcap_file):
            exit_error("%s does not exist." % pcap_file)
        if not port:
            port = 41234
        if not target:
            target = "127.0.0.1"
        run_as_asterix_replay()
        return

    # Beast server mode: serve pcap Beast stream for aeroswarm TcpBeastReceiver
    if beast_server:
        if not len(pcap_file):
            exit_error("--beast-server requires -f [pcap_file]")
        if not os.path.exists(pcap_file):
            exit_error("%s does not exist." % pcap_file)
        if not validate_port(port):
            exit_error("Invalid port!")
        run_as_beast_server()
        return

    # we are going run as a client send data to server
    if not listen:
        debugger("check if %s:%d is a valid address" %(target, port))
        if not len(target):
            exit_error("Must select a target host!")
        elif not validate_ip(target):
            exit_error("Invalid IP address!")  
        if not port:
            port = 6324
            info("Default connect to remote port 6324")
        elif not validate_port(port):
            exit_error("Invalid port!")
        debugger("check if %s is a valid file" %pcap_file)
        if not isinstance(speed, int):
            exit_error("Wrong type! Speed must be an integer!")
        if not len(pcap_file):
            exit_error("Must select a pcap/pcapng file!")
        elif not pcap_file.endswith('pcap') or pcap_file.endswith('pcapng'):
            exit_error("Wrong file type! Must select a pcap/pcapng file.")
        else:
            # check if pcap_file exists
            if os.path.exists(pcap_file):
                print("run as client")
            else:
                exit_error("%s does not exist." %pcap_file)
        if not len(interface):
            interface = get_local_interface()
            info("Default use the first avaliable interface: %s" %interface)

        run_as_client()

    # we are going to listen as a server
    else:
        # if no target is defined we listen on all networks
        if not len(target):
            target = "0.0.0.0"
            debugger("Default listen on all networks 0.0.0.0")
        elif not validate_ip(target):
            exit_error("Invalid IP address!")
        if not port:
            port = 6324
            debugger("Default listen on local port 6324")
        elif not validate_port(port):
            exit_error("Invalid port!")
        if not len(interface):
            interface = get_local_interface()
            info("Default use the first avaliable interface: %s" %interface)

        run_as_server()

main()