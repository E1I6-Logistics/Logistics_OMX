"""Broker loss must not tear down the robot/model process. No hardware operations."""


def start(client, host, port):
    client.reconnect_delay_set(min_delay=1, max_delay=5)
    client.max_queued_messages_set(128)
    client.connect_async(host, port, 60)
    client.loop_start()


def stop(client):
    # Request DISCONNECT before joining the network thread.
    try:
        client.disconnect()
    finally:
        client.loop_stop()
