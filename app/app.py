import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
import time
import sys
import yaml
import socket
import json
import errno
import threading


class DataError(Exception):
    pass


def configure(params, unconfigure=False):
    msgs = []

    for param, name, device_class, unit_of_measurement, extra in params:
        sensor_type = 'sensor'

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
            'value_template': f"{{{{ value_json.{param} }}}}"
        }
        if device_class:
            data['device_class'] = device_class
        if unit_of_measurement:
            data['unit_of_measurement'] = unit_of_measurement

        if extra:
            if 'type' in extra:
                sensor_type = extra['type']
                del extra['type']

            data.update(extra)

        if sensor_type == 'switch':
            data['command_topic'] = f'{topic}/{device_id}/set'
            data['payload_off'] = 'OFF'
            data['payload_on'] = 'ON'

        if data and data['entity_category'] == 'config':
            data['command_topic'] = f'{topic}/{device_id}/set/{param}'

        payload = json.dumps(data)
        msgs.append({
            'topic': f"homeassistant/{sensor_type}/{device_id}/{param}/config",
            'payload': payload if not unconfigure and 'delete' not in data else '',
            'qos': 2,
            'retain': True
        })
        print(f"SEND: homeassistant/{sensor_type}/{device_id}/{param}/config", payload)
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


def get_msg(sock, nonblocking=False):
    if nonblocking:
        sock.setblocking(False)
    msg = ''
    try:
        msg = sock.recv(4096)
    except socket.error as e:
        err = e.args[0]
        if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
            time.sleep(1)
            print('No data available')
            if nonblocking:
                sock.setblocking(True)
                return None
        else:
            # a "real" error occurred
            print(f"ERROR: {e}")
            sys.exit(1)
    return msg.decode()


def get_data(sock):
    msg = None
    try:
        msg = get_msg(sock, nonblocking=True)
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
    global current_state
    # print(f"INPUT: {input}")
    output = {
        'status': 'available',
    }

    if 'BoilerTempAct' in input:
        output['temp_boiler'] = round(
            float(input['BoilerTempAct']) / 100,
            temp_precision
        )

    if 'BoilerTempCmd' in input:
        output['temp_boiler_setting'] = round(
            float(input['BoilerTempCmd']) / 100,
            temp_precision
        )

    if 'BoilerHist' in input:
        output['hist_boiler_setting'] = round(
            float(input['BoilerHist']) / 100,
            temp_precision
        )

    if 'WeaTempAct' in input:
        output['temp_out'] = round(
            float(input['WeaTempAct']) / 100 + temp_out_correction,
            temp_precision
        )

    if 'CH1RoomTempAct' in input:
        output['temp_room'] = round(
            float(input['CH1RoomTempAct']) / 100 + temp_room_correction,
            temp_precision
        )

    if 'CH1RoomTempCmd' in input:
        output['temp_room_setting'] = round(
            float(input['CH1RoomTempCmd']) / 100,
            temp_precision
        )

    if 'CH1RoomHist' in input:
        output['hist_room_setting'] = round(
            float(input['CH1RoomHist']) / 100,
            temp_precision
        )


    if 'CH1Mode' in input:
        output['switch'] = {
            'Still_On': 'ON',
            'Stop': 'OFF'
        }[input['CH1Mode']]

        with current_state_lock:
            current_state = output['switch']

    if 'DevStatus' in input:
        dev_status = input['DevStatus']
        output['power'] = {
            '000': 0,
            '033': int(device_power / 3),
            '066': int(device_power / 3 * 2),
            '100': int(device_power)
        }[dev_status[3:6]]

    if 'BuModulMax' in input:
        output['power_setting'] = {
            '0': '33',
            '1': '66',
            '3': '100',
        }[input['BuModulMax']]

    if 'P033' in input:
        output['e_total'] = input['P033']

    if not output:
        raise DataError("Empty data set")

    with settings_lock:
        changed = False
        for param in output:
            if param in device_to_settings:
                payload = output[param]
                if payload != settings[device_to_settings[param][0]]:
                    settings[device_to_settings[param][0]] = payload
                    changed = True
        if changed:
            save_settings(settings)

    return input['Token'], output


def prep_settings(lock=True):
    if lock:
        settings_lock.acquire()
    data = dict(
        boiler_temp=int(settings['temp_boiler']*100),
        boiler_hist=int(settings['hist_boiler']*100),
        room_temp=int(settings['temp_room']*100),
        room_hist=int(settings['hist_room']*100),
        power={
            '33': 0,
            '66': 1,
            '100': 3,
        }[settings['power']],
    )
    if lock:
        settings_lock.release()
    return data


def on_connect(client, userdata, flags, rc):
    print("DEBUG: Connected rc: " + str(rc))
    client.subscribe(f'{topic}/{device_id}/set')
    client.subscribe(f'{topic}/{device_id}/set/#')


def on_subscribe(mosq, obj, mid, granted_qos):
    print("DEBUG: Subscribed: " + str(mid) + " " + str(granted_qos))


def on_message(client, userdata, msg):
    if not msg.topic.startswith(f'{topic}/{device_id}/set'):
        return

    print(f"RECV: {msg.topic} {msg.payload}")
    _, param = msg.topic.split('/set')
    print(f"PARAM: {param}")

    t_set = prep_settings()

    with current_state_lock:
        t_set['CH1Mode'] = {
            'ON': 'Still_On',
            'OFF': 'Stop',
        }[current_state]

    changed = False

    if param == '':
        payload = msg.payload.decode('utf-8')

        with current_state_lock:
            if payload != current_state:
                changed = True
                t_set['CH1Mode'] = {
                    'ON': 'Still_On',
                    'OFF': 'Stop',
                }[payload]
    else:
        param = param[1:]

    with settings_lock:
        settings_changed = False
        if param in device_to_settings:
            payload = device_to_settings[param][1](msg.payload.decode('utf-8'))
            print(f"PARAM: {param} {payload} -> {device_to_settings[param]}")
            if payload != settings[device_to_settings[param][0]]:
                settings[device_to_settings[param][0]] = payload
                changed = True
                settings_changed = True
        if settings_changed:
            save_settings(settings)
            t_set.update(prep_settings(lock=False))

    if changed:
        send_data(timel_sock, (
            f'BoilerTempCmd:{t_set["boiler_temp"]}'
            f',BoilerHist:{t_set["boiler_hist"]}'
            f',CH1Mode:{t_set["CH1Mode"]}'
            f',BuModulMax:{t_set["power"]}'
            f',CH1RoomTempCmd:{t_set["room_temp"]}'
            f',CH1RoomHist:{t_set["room_hist"]}'
        ))


def send_data(sock, to_send):
    try:
        from timel_token import codestr

        token, data = process_data(get_data(sock))

        coded_token = codestr(token)
        print(f"TOKEN: {token} | {coded_token}")
        if not coded_token:
            return

        req = (
            '{'
            + f'DevId:{device_encoded},DevPin:{device_pin},Token:{coded_token},FrameType:AppDataToChange,{to_send}'
            + '}\n'
        )
        print(f"SENDING: {req}")
        sock.send(req.encode())

        i = 0
        while True:
            time.sleep(1)
            i += 1
            req = f'{{DevId:{device_encoded},DevPin:{device_pin},FrameType:AppDataRqst}}\n'
            print(f"SENDING: {i} {req}")
            sock.send(req.encode())
            msg = get_msg(sock, nonblocking=True)
            if msg:
                if 'data' in msg:
                    send_mqtt(json.loads(msg)['data'][0])
                    break
            if i > 20:
                print(f"ERROR: No data after 20s!")
                break

    except Exception as e:
        print(f"ERROR: {e}")
        raise DataError(f"Error: {e}")


def send_mqtt(data):
    token, data = process_data(data)
    payload = json.dumps(data)
    print(f"SEND: {topic}/{device_id}", payload)
    publish.single(f"{topic}/{device_id}", payload, qos=2, hostname=conf.get('mqtt_broker', 'mqtt'))


def save_settings(settings):
    with open("/app/settings.yml", 'w') as f:
        yaml.dump(settings, f)

###


with open("/app/config.yml", 'r') as f:
    conf = yaml.safe_load(f)

settings_lock = threading.Lock()
with open("/app/settings.yml", 'r') as f:
    settings = yaml.safe_load(f)

topic = conf.get('topic', 'elterm')
device_id = conf.get('device_id', 'ELTERM')
device_encoded = conf.get('device_encoded', 'ELTERM')
device_power = int(conf.get('device_power', 9000))
device_pin = conf.get('device_pin', 'ZZZZ')
setting_mode = conf.get('setting_mode', 'slider')
timel_server = conf.get('timel_server')
timel_port = conf.get('timel_port')
temp_out_correction = float(conf.get('temp_out_correction', 0))
temp_room_correction = float(conf.get('temp_room_correction', 0))
temp_precision = int(conf.get('temp_precision', 1))
current_state = None
current_state_lock = threading.Lock()

devices = [
    ['status', 'Status', None, None, None],
    ['temp_out', 'Temperature Outside', 'temperature', '°C', {"icon": "mdi:thermometer"}],
    ['temp_room', 'Temperature Inside', 'temperature', '°C', {"icon": "mdi:thermometer"}],
    ['temp_boiler_setting', 'Boiler Target', 'temperature', '°C', {
        "type": "number",
        # "delete": True,
        "entity_category": "config",
        "icon": "mdi:thermometer",
        "min": 5,
        "max": 85,
        "mode": setting_mode,
    }],
    ['hist_boiler_setting', 'Boiler Hyst.', 'temperature', '°C', {
        "type": "number",
        # "delete": True,
        "entity_category": "config",
        "icon": "mdi:thermometer",
        "min": 2,
        "max": 6,
        "mode": setting_mode,
    }],
    ['temp_room_setting', 'Room Target', 'temperature', '°C', {
        "type": "number",
        # "delete": True,
        "entity_category": "config",
        "icon": "mdi:thermometer",
        "min": 5,
        "max": 30,
        "step": 0.5,
        "mode": setting_mode,
    }],
    ['hist_room_setting', 'Room Hyst.', 'temperature', '°C', {
        "type": "number",
        # "delete": True,
        "entity_category": "config",
        "icon": "mdi:thermometer",
        "min": 0.1,
        "max": 2.0,
        "step": 0.1,
        "mode": setting_mode,
    }],
    ['power_setting', 'Power %', None, '%', {
        "type": "select",
        "options": [
            "33",
            "66",
            "100",
        ],
        # "delete": True,
        "entity_category": "config",
        "mode": setting_mode,
    }],
    ['temp_boiler', 'Temperature Boiler', 'temperature', '°C', {"icon": "mdi:thermometer"}],
    ['e_total', 'Total Energy Used', 'energy', 'kWh', {'state_class': 'total_increasing'}],
    ['power', 'Power', 'power', 'W', None],
    ['switch', 'Switch', None, None, {'type': 'switch', 'off_delay': 10, 'on_delay': 10}],
]

device_to_settings = {
    'temp_boiler_setting': ['temp_boiler', int],
    'hist_boiler_setting': ['hist_boiler', int],
    'temp_room_setting': ['temp_room', lambda x: round(round(float(x)/50, 2)*50, 1)],
    'hist_room_setting': ['hist_room', lambda x: round(float(x), 1)],
    'power_setting': ['power', str],
}

set_state('online')
configure(devices)
# configure(devices, unconfigure=True)
timel_sock = connect(timel_server, timel_port)

mqttsc = mqtt.Client()
mqttsc.on_message = on_message
mqttsc.on_connect = on_connect
mqttsc.on_subscribe = on_subscribe
mqttsc.connect(conf.get('mqtt_broker', 'mqtt'))
mqttsc.loop_start()

while True:
    try:
        set_state('online')
        send_mqtt(get_data(timel_sock))

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
