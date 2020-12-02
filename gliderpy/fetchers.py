"""
Helper methods to fetch glider data from multiple ERDDAP serves

"""

import functools

from typing import Optional

import cartopy.crs as ccrs
import matplotlib.pyplot as plt
import pandas as pd

from erddapy import ERDDAP

OptionalStr = Optional[str]

# This is hardcoded to the IOOS glider DAC.
# We aim to support more sources in the near future.
_server = "https://gliders.ioos.us/erddap"

server_vars = {
    "https://gliders.ioos.us/erddap": [
        "depth",
        "latitude",
        "longitude",
        "salinity",
        "temperature",
        "time",
    ],
    "http://www.ifremer.fr/erddap": [
        "time",
        "latitude",
        "longitude",
        "PSAL",
        "TEMP",
        "PRES",
        "platform_deployment",
    ],
    "https://erddap-uncabled.oceanobservatories.org/uncabled/erddap": [
        "latitude",
        "longitude",
        "ctdgv_m_glider_instrument_practical_salinity",
        "ctdgv_m_glider_instrument_sci_water_temp",
        "ctdgv_m_glider_instrument_sci_water_pressure_dbar",
        "time",
        "quality_flag",
        "trajectory"
    ]
}


class GliderDataFetcher(object):
    """
    Args:
        server: a glider ERDDAP server URL

    Attributes:
        dataset_id: a dataset unique id.
        constraints: download constraints, default None (opendap-like url)

    """

    def __init__(self, server=_server):
        self.fetcher = ERDDAP(
            server=server,
            protocol="tabledap",
        )
        self.fetcher.variables = server_vars[server]
        self.fetcher.dataset_id: OptionalStr = None

    def to_pandas(self):
        """
        Fetches data from the server and reads into a pandas dataframe

        :return: pandas dataframe with datetime UTC as index
        """
        return self.fetcher.to_pandas(
            index_col="time (UTC)",
            parse_dates=True,
        )

    def query(self, min_lat, max_lat, min_lon, max_lon, start_time, end_time):
        """
        Takes user supplied geographical and time constraints and adds them to the query

        :param min_lat: southernmost lat
        :param max_lat: northermost lat
        :param min_lon: westernmost lon (-180 to +180)
        :param max_lon: easternmost lon (-180 to +180)
        :param start_time: start time, can be datetime object or string
        :param end_time: end time, can be datetime object or string
        :return: search query with argument constraints applied
        """
        self.fetcher.constraints = {
            "time>=": start_time,
            "time<=": end_time,
            "latitude>=": min_lat,
            "latitude<=": max_lat,
            "longitude>=": min_lon,
            "longitude<=": max_lon,
        }
        return self

    def platform(self, platform):
        """

        :param platform: platform and deployment id from ifremer
        :return: search query with platform constraint applied
        """
        self.fetcher.constraints["platform_deployment="] = platform
        return self


class DatasetList:
    """Search servers for glider dataset ids. Defaults to the string "glider"


    Attributes:
        e: an ERDDAP server instance
        search_terms: A list of terms to search the server for. Multiple terms will be combined as AND

    """

    def __init__(self, server=_server):
        self.e = ERDDAP(
            server=server,
            protocol="tabledap",
        )

    @functools.lru_cache(maxsize=None)
    def _get_ids(self, search_terms):
        """Thin wrapper where inputs can be hashed for lru_cache."""
        dataset_ids = pd.Series(dtype=str)
        for term in search_terms:
            url = self.e.get_search_url(search_for=term, response="csv")

            dataset_ids = dataset_ids.append(
                pd.read_csv(url)["Dataset ID"], ignore_index=True
            )
        self.dataset_ids = dataset_ids.str.split(";", expand=True).stack().unique()

        return self.dataset_ids

    def get_ids(self, search_terms=["glider"]):
        """Search the database using a user supplied list of comma separated strings
        :return: Unique list of dataset ids
        """
        search_terms = tuple(search_terms)
        return self._get_ids(search_terms)
