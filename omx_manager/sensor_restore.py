"""Camera-only pending-transaction recovery in a bounded, owned child process."""
import argparse
import os
import threading
from .config import load
from .commissioning import camera_operation


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--parent-pid',type=int,required=True)
    parser.add_argument('--lease-fd',type=int,required=True)
    args=parser.parse_args()
    if os.getppid()!=args.parent_pid:raise RuntimeError('Recovery supervisor is no longer the parent')
    os.fstat(args.lease_fd)  # Keep the inherited device lock until process exit.
    def parent_guard():
        while os.getppid()==args.parent_pid:threading.Event().wait(.5)
        # Parent vanished: retain the pending restoration journal and close all
        # descriptors at exit. Never leave an orphan camera owner behind.
        os._exit(74)
    threading.Thread(target=parent_guard,daemon=True).start()
    camera_operation(load(args.config),'basket-restore-exposure')


if __name__=='__main__':main()
