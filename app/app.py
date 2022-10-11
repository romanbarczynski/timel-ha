import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
import json
import time
import sys
import yaml
import socket
import json
import errno


class DataError(Exception):
    pass


def configure(params, unconfigure=False):
    msgs = []

    for param, name, device_class, unit_of_measurement, extra in params:
        data = {
            'availability': [{'topic': f'{topic}/bridge/state'}],
            'device': {
                'identifiers': [f'{topic}_{device_id}'],
                'manufacturer': "TIMEL",
                'model': 'GOLD',
                'name': 'TIMEL'
            },
            'entity_category': 'diagnostic',
            'json_attributes_topic': f'{topic}/{device_id}',
            'name': f'TIMEL {name}',
            'state_topic': f'{topic}/{device_id}',
            'unique_id': f'{topic}_{device_id}_{param}',
            'value_template': f"{{{{ value_json['update']['{param}'] }}}}"
        }
        if device_class:
            data['device_class'] = device_class
        if unit_of_measurement:
            data['unit_of_measurement'] = unit_of_measurement
        if extra:
            data.update(extra)
        payload = json.dumps(data)
        msgs.append({
            'topic': f"homeassistant/sensor/{device_id}/{param}/config",
            'payload': payload if not unconfigure else '',
            'qos': 2,
            'retain': True
        })
        print(f"SEND: homeassistant/sensor/{device_id}/{param}/config", payload)
    print("DEBUG: ", publish.multiple(msgs, hostname=conf.get('mqtt_broker', 'mqtt')))


def set_state(state):
    print(f"SEND: {topic}/bridge/state", state)
    publish.single(f"{topic}/bridge/state", state, qos=2, hostname=conf.get('mqtt_broker', 'mqtt'))


def connect(timel_server, timel_port):
    print("Connecting to TIMEL Server...")
    timel_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    timel_sock.connect((timel_server, timel_port))
    print("Connected!")
    time.sleep(1)
    print(get_msg(timel_sock).strip())
    return timel_sock


def get_msg(sock):
    msg = ''
    try:
        msg = sock.recv(4096)
    except socket.error as e:
        err = e.args[0]
        if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
            time.sleep(1)
            print('No data available')
        else:
            # a "real" error occurred
            print(f"ERROR: {e}")
            sys.exit(1)
    return msg.decode()


def get_data(sock):
    try:
        req = f'{{DevId:{device_encoded},DevPin:{device_pin},FrameType:AppDataRqst}}\n'
        # print(f"SENDING: {req}")
        sock.send(req.encode())
        time.sleep(1)
        msg = get_msg(sock)
        return json.loads(msg)['data'][0]

    except Exception as e:
        print(f"ERROR: {msg} {e}")
        raise DataError(f"Received '{msg}' - error: {e}")


def process_data(input):
    output = {}
    if 'WeaTempAct' in input:
        output['temp_out'] = round(float(input['WeaTempAct']) / 100 + temp_out_correction, 2)
    if 'CH1RoomTempAct' in input:
        output['temp_room'] = round(float(input['CH1RoomTempAct']) / 100 + temp_room_correction, 2)

    if 'P033' in input:
        output['e_total'] = input['P033']

    if not output:
        raise DataError("Empty data set")

    return output


###

conf = None
with open("/app/config.yml", 'r') as f:
    conf = yaml.safe_load(f)

topic = conf.get('topic', 'zevercom')
device_id = conf.get('device_id', 'ELTERM')
device_encoded = conf.get('device_encoded', 'ELTERM')
device_pin = conf.get('device_pin', 'ZZZZ')
timel_server = conf.get('timel_server')
timel_port = conf.get('timel_port')
temp_out_correction = float(conf.get('temp_out_correction', 0))
temp_room_correction = float(conf.get('temp_room_correction', 0))

devices = [
    ['status', 'Status', None, None, None],
    ['temp_out', 'Temperature Outside', 'temperature', '°C', {"icon": "mdi:thermometer"}],
    ['temp_room', 'Temperature Inside', 'temperature', '°C', {"icon": "mdi:thermometer"}],
    ['e_total', 'Total Energy Used', 'energy', 'kWh', {'state_class': 'total_increasing'}],
]

set_state('online')
configure(devices)
# configure(devices, unconfigure=True)
timel_sock = connect(timel_server, timel_port)

while True:
    try:
        set_state('online')
        data = process_data(get_data(timel_sock))
        payload = json.dumps({'update': data})
        print(f"SEND: {topic}/{device_id}", payload)
        publish.single(f"{topic}/{device_id}", payload, qos=2, hostname=conf.get('mqtt_broker', 'mqtt'))

        time.sleep(29)

    except (DataError, socket.error) as e:
        print(f"ERROR: {e}")
        try:
            timel_server.close()
        except Exception as e:
            print(f"ERROR: {e}")
            pass
        print("Reconnecting in 30s")
        time.sleep(30)
        timel_sock = connect(timel_server, timel_port)


    except Exception as e:
        print(f"ERROR: {e}")
        break

set_state('offline')
