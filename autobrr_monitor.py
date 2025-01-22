import os
import logging
import requests
from prometheus_api_client import PrometheusConnect
from qbittorrentapi import Client
from typing import List, Dict, Union

# Configure logging level based on the DEBUG flag
DEBUG = int(os.getenv("DEBUG", "0"))
logging.basicConfig(
    level=logging.DEBUG if DEBUG == 1 else logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Configuration parameters
QBITTORRENT_HOST = os.environ.get('QBITTORRENT_HOST')
QBITTORRENT_PORT = os.environ.get('QBITTORRENT_PORT', '8080')
QBITTORRENT_USERNAME = os.environ.get('QBITTORRENT_USERNAME')
QBITTORRENT_PASSWORD = os.environ.get('QBITTORRENT_PASSWORD')

PROMETHEUS_URL = f"http://{os.environ.get('PROMETHEUS_HOST')}:{os.environ.get('PROMETHEUS_PORT')}"

AUTOBRR_URL = f"http://{os.environ.get('AUTOBRR_HOST')}:{os.environ.get('AUTOBRR_PORT')}/api"
AUTOBRR_API_KEY = os.environ.get('AUTOBRR_API_KEY')
TORRENT_CATEGORY_FILTER = os.environ.get('TORRENT_CATEGORY_FILTER', "autobrr")

GLOBAL_UPLOAD_THRESHOLD_BYTES = int(os.environ.get('GLOBAL_UPLOAD_THRESHOLD_BYTES', 1048576))  # 1 MB/s
GLOBAL_TIME_HORIZON_SECONDS = int(os.environ.get('GLOBAL_TIME_HORIZON_SECONDS', 43200))  # 12 hours
TORRENT_UPLOAD_THRESHOLD_BYTES = int(os.environ.get('TORRENT_UPLOAD_THRESHOLD_BYTES', 10240))  # 10 KB/s
TORRENT_TIME_HORIZON_SECONDS = int(os.environ.get('TORRENT_TIME_HORIZON_SECONDS', 432000))  # 5 days

MAX_TORRENTS_SIZE_BYTES = int(os.environ.get('MAX_TORRENTS_SIZE_BYTES', 1099511627776))  # 1 TB
# Policy can be strict, relaxed
ENFORCE_MAX_SIZE_POLICY = os.environ.get('ENFORCE_MAX_SIZE_POLICY', 'relaxed')

AUTOBRR_INDEXER_NAME = os.environ.get('AUTOBRR_INDEXER_NAME')

SIMULATION_MODE = int(os.getenv("SIMULATION_MODE", "0")) == 1

# Prometheus setup
prometheus = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)

def bytes_to_readable_str(bytes: int, unit: str = 'KB', decimals: int = 2) -> str:
    """
    Convert bytes to a human-readable format.

    Args:
        bytes (int): The number of bytes to convert.
        unit (str): The unit to convert to (KB, MB, GB, TB). Defaults to 'MB'.
        decimals (int): The number of decimal places to display. Defaults to 3.

    Returns:
        str: The human-readable string representation of the bytes.
    """
    units = {'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
    if unit not in units:
        raise ValueError("Invalid unit. Choose from 'KB', 'MB', 'GB', or 'TB'.")
    return f"{bytes / units[unit]:.{decimals}f} {unit}"

def query_prometheus(metric: str, range_duration: int) -> List[Dict[str, Union[str, List[List[Union[str, float]]]]]]:
    """
    Query Prometheus for a specific metric and range duration.

    Args:
        metric (str): The metric to query.
        range_duration (int): The range duration in seconds.

    Returns:
        List[Dict]: The results from the Prometheus query.
    """
    try:
        query = f"{metric}[{range_duration}s]"
        result = prometheus.custom_query(query=query)
        return result
    except Exception as e:
        logging.error(f"Failed to query Prometheus: {e}")
        return []

def calculate_average_upload_speed(metric: str, range_duration: int) -> float:
    """
    Calculate the average upload speed from Prometheus metrics.

    Args:
        metric (str): The metric to query.
        range_duration (int): The range duration in seconds.

    Returns:
        float: The average upload speed in bytes per second.
    """
    results = query_prometheus(metric, range_duration)
    total_bytes = 0
    data_points = 0

    for torrent in results:
        for value in torrent["values"]:
            total_bytes += float(value[1])
            data_points += 1

    avg_speed_bytes = total_bytes / data_points if data_points > 0 else 0
    return avg_speed_bytes

def get_qbittorrent_client() -> Client:
    """
    Connect to the qBittorrent client.

    Returns:
        Client: The qBittorrent client instance.
    """
    logging.debug("Connecting to qBittorrent")
    try:
        client = Client(
            host=f"http://{QBITTORRENT_HOST}:{QBITTORRENT_PORT}",
            username=QBITTORRENT_USERNAME,
            password=QBITTORRENT_PASSWORD
        )
        return client
    except Exception as e:
        logging.error(f"Failed to connect to qbittorrent: {e}")


def toggle_autobrr_indexers(enable: bool, reason: str) -> None:
    """
    Enable or disable Autobrr indexers based on the `enable` flag.

    Args:
        enable (bool): Whether to enable or disable the indexers.
    """
    try:
        response = requests.get(f"{AUTOBRR_URL}/indexer", headers={"X-API-Token": AUTOBRR_API_KEY})
        response.raise_for_status()
        indexers = response.json()

        for indexer in indexers:
            # Only log if the status is changing
            if enable != indexer['enabled']:
                logging.info(reason)

            if AUTOBRR_INDEXER_NAME.lower() == 'all' or indexer['name'] == AUTOBRR_INDEXER_NAME:
                logging.debug(f"{'SIMULATION: ' if SIMULATION_MODE else ''}Indexer '{indexer['name']}' (ID: {indexer['id']}) {'enabled' if enable else 'disabled'}")
                if not SIMULATION_MODE:
                    response = requests.patch(
                        f"{AUTOBRR_URL}/indexer/{indexer['id']}/enabled",
                        headers={"X-API-Token": AUTOBRR_API_KEY},
                        json={"enabled": enable}
                    )
                    # Check if the request was successful
                    response.raise_for_status()

    except requests.RequestException as e:
        logging.error(f"Error toggling Autobrr indexers: {e}")

def enforce_disk_space_limit(qb: Client, torrents: List[Dict[str, Union[str, int]]], max_size_bytes: int) -> None:
    """
    Enforce a disk space limit by removing torrents exceeding the limit.

    Args:
        qb (Client): The qBittorrent client instance.
        torrents (List[Dict]): The list of torrents to check.
        max_size_bytes (int): The maximum allowed disk space in bytes.
    """
    # Sort torrents by average upload speed (descending)
    torrents = sorted(torrents, key=lambda t: t['avg_upload_speed'], reverse=True)

    cumulative_size = 0
    selected_torrents = []
    delete_torrents = []

    for torrent in torrents:
        torrent_size = torrent['size_bytes']
        if cumulative_size + torrent_size <= max_size_bytes:
            cumulative_size += torrent_size
            selected_torrents.append(torrent)
            logging.debug(f"Current torrent {torrent['name']} of size {bytes_to_readable_str(torrent_size, 'GB')}, current sum {bytes_to_readable_str(cumulative_size, 'GB')} does not exceed maximum {bytes_to_readable_str(max_size_bytes, 'GB')}.")
        else:
            logging.warning(f"Scheduled torrent removal: {torrent['name']} (hash: {torrent['hash']}) of size {bytes_to_readable_str(torrent_size, 'GB')}, current sum {bytes_to_readable_str(cumulative_size, 'GB')} would exceed maximum {bytes_to_readable_str(max_size_bytes, 'GB')}.")
            delete_torrents.append(torrent)

    if not SIMULATION_MODE:
        delete_torrent_hashes = [torrent['hash'] for torrent in delete_torrents]
        qb.torrents_delete(delete_files=True, torrent_hashes=delete_torrent_hashes)
        pass
    
    if len(delete_torrents) > 0:
        logging.info(f"{'SIMULATION: ' if SIMULATION_MODE else ''}Removed torrents due to disk space limit: {[torrent['name'] for torrent in delete_torrents]}")

def torrent_upload_threshold_filter(qb: Client, completed_torrents: List[Dict[str, Union[str, int]]]) -> List[Dict[str, Union[str, int]]]:
    """
    Enforce the upload threshold filter on torrents.

    Args:
        qb (Client): The qBittorrent client instance.
        completed_torrents (List[Dict]): The list of completed torrents.

    Returns:
        List[Dict]: The list of forced seeding torrents.
    """
    # In completed_torrents, check which ones don't meet minimum upload speed threshold
    # and remove them if they don't
    # Any torrents that do meet the threshold should be kept in this step, and set to 
    # "Seed" if it's been set to "Completed"
    forced_started_torrents = []
    delete_torrents = []
    for torrent in completed_torrents:
        # Remove torrent if it doesn't meed upload speed threshold
        if torrent['avg_upload_speed'] < TORRENT_UPLOAD_THRESHOLD_BYTES:
            logging.debug(f"Scheduled for removal: {torrent['name']} (hash: {torrent['hash']}), average upload speed {bytes_to_readable_str(torrent['avg_upload_speed'])}/s < threshold {bytes_to_readable_str(TORRENT_UPLOAD_THRESHOLD_BYTES)}/s")
            delete_torrents.append(torrent)
        # Ensure status is set to "Seeding" and not "Completed" if it does meet minimum upload requirements
        else:
            logging.debug(f"Keeping torrent: {torrent['name']} (hash: {torrent['hash']}), average upload speed {bytes_to_readable_str(torrent['avg_upload_speed'])}/s >= threshold {bytes_to_readable_str(TORRENT_UPLOAD_THRESHOLD_BYTES)}/s")
            if torrent['status'] == 'stoppedUP':
                forced_started_torrents.append(torrent)

    if not SIMULATION_MODE:
        delete_torrent_hashes = [torrent['hash'] for torrent in delete_torrents]
        qb.torrents_delete(delete_files=True, torrent_hashes=delete_torrent_hashes)
        pass
    
    if len(delete_torrents) > 0:
        logging.info(f"{'SIMULATION: ' if SIMULATION_MODE else ''}Removed torrents due to upload speed threshold: {[torrent['name'] for torrent in delete_torrents]}")

    # Resume seeding for completed torrents
    if not SIMULATION_MODE:
        force_start_torrent_hashes = [torrent['hash'] for torrent in forced_started_torrents]
        qb.torrents_set_force_start(enable=True, torrent_hashes=force_start_torrent_hashes)
        pass
    
    if len(forced_started_torrents) > 0:
        logging.info(f"{'SIMULATION: ' if SIMULATION_MODE else ''}Resumed seeding for completed torrents: {[torrent['name'] for torrent in forced_started_torrents]}")

    return forced_started_torrents

def process_torrents(qb: Client, max_size_bytes: int) -> List[Dict[str, Union[str, int, float]]]:
    """
    Enforces multiple rules
    1) Removes any (Completed/Forced Seeding) torrents whose average upload speeds drop below the threshold
    2) Of the remaining Forced Seeding torrents, removes the slowest if they exceed the allocated disk space for autobrr torrents
    """
    torrents = qb.torrents.info()
    completed_torrents = []
    remaining_seed_time_torrents = []

    # First sort torrents into categories: remaining_seed_time_torrents or completed_torrents
    # Here we also calculate the average upload speed for each torrent in the past TORRENT_TIME_HORIZON_SECONDS seconds
    used_space = 0
    for torrent in torrents:
        if torrent.category == TORRENT_CATEGORY_FILTER:
            avg_torrent_upload = calculate_average_upload_speed(
                f'qbittorrent_torrent_upload_speed_bytes{{name="{torrent.name}"}}',
                TORRENT_TIME_HORIZON_SECONDS
            )

            remaining_seed_time = torrent.eta
            torrent_data = {
                'name': torrent.name,
                'hash': torrent.hash,
                'size_bytes': torrent.size,
                'avg_upload_speed': avg_torrent_upload,
                'remaining_seed_time': remaining_seed_time,
                'status': torrent.state
            }

            if remaining_seed_time > 0 and not torrent.state == 'stoppedUP':
                remaining_seed_time_torrents.append(torrent_data)
                logging.debug(f"Keeping torrent {torrent_data['name']} as it has remaining seed time of {torrent.eta}")
            elif torrent.state == 'stoppedUP':
                completed_torrents.append(torrent_data)

            used_space += torrent.size

    # Filter torrents based on average upload speed
    # Completed torrents (ie, torrents with no more seed time left) are kept or removed, depending on if
    # their average upload speed (calculated in previous loop) exceeds the threshold or not
    forced_seeding_torrents = torrent_upload_threshold_filter(qb, completed_torrents)

    # Enforce disk space
    free_space = max_size_bytes - used_space

    logging.debug(f"Used space by torrents with remaining seed time: {bytes_to_readable_str(used_space, 'GB')}, remaining free space: {bytes_to_readable_str(free_space, 'GB')}")

    if used_space <= max_size_bytes:
        # There's free space left, enforce disk space limit for forced seeding torrents
        enforce_disk_space_limit(qb, forced_seeding_torrents, free_space)
    else:
        # Disk space exceeded by remaining seed time torrents; remove all forced seeding torrents
        if ENFORCE_MAX_SIZE_POLICY == 'strict':
            logging.debug(f"Disk space limit exceeded by {bytes_to_readable_str(free_space, 'GB')}. Removing all forced seeding torrents as free space is {bytes_to_readable_str(free_space, 'GB')}.")
            enforce_disk_space_limit(qb, forced_seeding_torrents, 0)
        elif ENFORCE_MAX_SIZE_POLICY == 'relaxed':
            logging.debug(f"Disk space limit exceeded by {bytes_to_readable_str(free_space, 'GB')}. Keeping forced seeding torrents due to relaxed policy.")
    
def main():
    qb = get_qbittorrent_client()

    # Process torrents based on remaining seed time (eta), average upload speed and allocated disk space for autobrr
    process_torrents(qb, MAX_TORRENTS_SIZE_BYTES)

    # Check global upload speed and toggle Autobrr
    avg_global_upload = calculate_average_upload_speed(
        "qbittorrent_torrent_upload_speed_bytes",
        GLOBAL_TIME_HORIZON_SECONDS
    )

    total_used_space = sum(t.size for t in qb.torrents.info() if t.category == TORRENT_CATEGORY_FILTER)

    # Turn on or off Autobrr depending on global upload threshold reached or maximum allocated storage exceeded
    if avg_global_upload < GLOBAL_UPLOAD_THRESHOLD_BYTES and total_used_space < MAX_TORRENTS_SIZE_BYTES:
        reason = f"Switching autobrr on because {bytes_to_readable_str(avg_global_upload)}/s < {bytes_to_readable_str(GLOBAL_UPLOAD_THRESHOLD_BYTES)}/s, and {bytes_to_readable_str(total_used_space, 'GB')} GB (total used space) < {bytes_to_readable_str(MAX_TORRENTS_SIZE_BYTES, 'GB')} (Max. allocated space)"
        toggle_autobrr_indexers(True, reason)
    else:
        reason = f"Switching autobrr off because {bytes_to_readable_str(avg_global_upload)}/s => {bytes_to_readable_str(GLOBAL_UPLOAD_THRESHOLD_BYTES)}/s, or {bytes_to_readable_str(total_used_space, 'GB')} (total used space) >= {bytes_to_readable_str(MAX_TORRENTS_SIZE_BYTES, 'GB')} (Max. allocated space)"
        toggle_autobrr_indexers(False, reason)

if __name__ == "__main__":
    main()