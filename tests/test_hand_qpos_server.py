import socket
import time
import unittest

import numpy as np

from hand_qpos_server import read_latest_qpos
from qpos_protocol import SocketLineReader, encode_message, make_hello_message, make_qpos_message


class ReadLatestQposTest(unittest.TestCase):
    def test_returns_buffered_latest_frame_without_waiting_for_socket_timeout(self):
        server_sock, client_sock = socket.socketpair()
        try:
            server_sock.settimeout(0.2)
            reader = SocketLineReader(server_sock)

            client_sock.sendall(encode_message(make_hello_message("left", time.time())))
            for seq in range(3):
                qpos = np.full((5, 4), seq, dtype=np.float64)
                client_sock.sendall(encode_message(make_qpos_message(seq, qpos, time.time())))

            start = time.monotonic()
            message, dropped = read_latest_qpos(reader)
            elapsed = time.monotonic() - start

            self.assertLess(elapsed, 0.05)
            self.assertEqual(message["seq"], 2)
            self.assertEqual(dropped, 2)
        finally:
            server_sock.close()
            client_sock.close()


if __name__ == "__main__":
    unittest.main()
