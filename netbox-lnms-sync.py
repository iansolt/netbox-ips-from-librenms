import pandas as pd
import requests
import logging
from config import NETBOX_TOKEN, LIBRE_TOKEN, NETBOX_URL, VERIFY_SSL, LIBRE_URL, LOG_FILE

HEADERS_NETBOX_PULL = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Accept": "application/json; indent=4"
}

HEADERS_NETBOX_PUSH = {
    'Authorization': f"Token {NETBOX_TOKEN}",
    'Content-Type': 'application/json',
}

HEADERS_LIBRE = {
    "X-Auth-Token": LIBRE_TOKEN
}

# configure logging
logging.basicConfig(filename=LOG_FILE, filemode='a', format='%(asctime)s %(name)s - %(levelname)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S', level=logging.INFO)

def netbox_api_to_dataframe(url=f'{NETBOX_URL}api/ipam/ip-addresses/?limit=0', headers=HEADERS_NETBOX_PULL):
# make API call to netbox and return IP addresses as a dataframe
    logging.info('Pulling data from NetBox')
    response = requests.get(url=url, headers=headers, verify=VERIFY_SSL)
    if response.status_code == 200:
        json_data = response.json()
        # netbox returns IP info under the results field
        df = pd.DataFrame.from_dict(json_data['results'])
        # expand the nested custom fields and attach them to the primary dataframe
        custom_fields = pd.json_normalize(df['custom_fields'])
        df = pd.concat([df, custom_fields], axis=1)
        # separate address into ip and mask. expand df to accommodate new columns
        df[['ip', 'mask']] = df['address'].str.split('/', expand=True)
        # filter dataframe to relevant columns
        df = df[['ip', 'mask', 'id']]
        logging.info(f'Retrieved {len(df)} IP Address records from NetBox')
        return df
    else:
        return logging.warning(f'NetBox returned an unexpected response in function netbox_api_to_dataframe:\n{response.json()}')

def libre_api_to_dataframe(url=f'{LIBRE_URL}api/v0/devices?all', headers=HEADERS_LIBRE):
# make API call to librenms and return devices as a dataframe
    logging.info('Pulling data from LibreNMS')
    response = requests.get(url=url, headers=headers, verify=VERIFY_SSL)
    if response.status_code == 200:
        json_data = response.json()
        # libre returns device info under the devices field
        df = pd.DataFrame.from_dict(json_data['devices'])
        # filter dataframe to relevant columns
        df = df[['ip', 'device_id', 'sysName', 'sysDescr', 'hardware', 'os', 'last_polled', 'serial']]
        logging.info(f'Retrieved {len(df)} device records from LibreNMS')
        return df
    else:
        return logging.warning(f'LibreNMS returned an unexpected response in function libre_api_to_dataframe:\n{response.json()}')

def post_to_netbox(records_to_post, url=f'{NETBOX_URL}api/ipam/ip-addresses/', headers=HEADERS_NETBOX_PUSH):
# create new /32s in netbox from device IPs in librenms
    logging.info('Creating new devices in NetBox')
    data = []
     # iterate through the dataframe and create a JSON object for each IP
    for _, row in records_to_post.iterrows():
        data.append({
            'address': row['ip'] + '/32',
            'status': 'active'
        })
    response = requests.post(url=url, json=data, headers=headers, verify=VERIFY_SSL)
    if response.status_code == 201:
        return logging.info(f'Created {len(records_to_post)} IP records')
    else:
        return logging.warning(f'NetBox returned an unexpected response in function post_to_netbox:\n{response.status_code} ')
    
def patch_to_netbox(records_to_patch, url=f'{NETBOX_URL}api/ipam/ip-addresses/', headers=HEADERS_NETBOX_PUSH):
# update existing IP addresses in netbox with polling info from librenms
    logging.info('Updating NetBox with polling data')
    data = []
    # iterate through the dataframe and create a JSON object for each IP. custom fields map to librenms device attributes
    for _, row in records_to_patch.iterrows():
        data.append({
            'id': row['id'],
            'status': 'active',
            'custom_fields': {
                'device_id': str(row['device_id']),
                'sysName': str(row['sysName']),
                'sysDescr': str(row['sysDescr']),
                'hardware': str(row['hardware']),
                'os': str(row['os']),
                'last_polled': str(row['last_polled']),
                'serial': str(row['serial'])
            }
        })
    response = requests.patch(url=url, json=data, headers=headers, verify=VERIFY_SSL)
    if response.status_code == 200:
        return logging.info(f'Updated {len(records_to_patch)} IP records')
    else:
        return logging.warning(f'NetBox returned an unexpected response in function patch_to_netbox:\n{response.json()}')

def main():
    logging.info('Initiating sync')
    # Make API calls and convert responses to DataFrames
    df_netbox = netbox_api_to_dataframe()
    df_lnms = libre_api_to_dataframe()
    # get list of records from df_lnms with IPs which do not exist in netbox
    records_to_post = df_lnms[~df_lnms['ip'].isin(df_netbox['ip'])]
    post_to_netbox(records_to_post=records_to_post)
    # if no new IPs were created, go straight to patching
    if records_to_post.empty:
        pass
    # if new IPs were created, refresh df_netbox with records to be updated
    else:
        df_netbox = netbox_api_to_dataframe()
    # join tables on IP to relate netbox OIDs with polling data from libre
    records_to_patch = pd.merge(left=df_lnms, right=df_netbox, how='outer', on='ip')
    # filter out netbox entries which don't exist in libre
    records_to_patch.dropna(subset=['device_id'], inplace=True)
    # fix device id being returned as a float
    records_to_patch['device_id'] = records_to_patch['device_id'].astype(int)
    patch_to_netbox(records_to_patch=records_to_patch)
    logging.info('Completed sync')


if __name__ == '__main__':
    main()