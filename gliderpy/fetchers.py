"""Helper methods to fetch glider data from multiple ERDDAP serves."""

import datetime
import functools
from copy import copy
from numbers import Number

import httpx
import pandas as pd
import stamina
from erddapy import ERDDAP
from erddapy.core.url import urlopen

from gliderpy.servers import (
    server_parameter_rename,
    server_vars,
)

OptionalBool = bool | None
OptionalDF = pd.DataFrame | None
OptionalDict = dict | None
OptionalList = list[str] | tuple[str] | None
OptionalStr = str | None
OptionalNum = Number | None
# Should we add more or datetime.datetime catches all?
OptionalDateTime = datetime.datetime | str

# Defaults to the IOOS glider DAC.
_server = "https://gliders.ioos.us/erddap"


@stamina.retry(on=httpx.HTTPError, attempts=3)
def _call_erddapy(glider_grab: "GliderDataFetcher") -> pd.DataFrame:
    """Temporary workaround until we move optional stamina to erddapy."""
    return glider_grab.fetcher.to_pandas()


@functools.lru_cache(maxsize=128)
def _to_pandas(
    glider_grab: "GliderDataFetcher",
    *,
    query: OptionalBool = True,
) -> pd.DataFrame:
    """Thin wrapper to cache results when multiple datasets are requested."""
    df_all = {}
    glider_grab_copy = copy(glider_grab)
    if query:
        dataset_ids = glider_grab_copy.datasets["Dataset ID"]
    else:
        dataset_ids = glider_grab_copy.dataset_ids

    for dataset_id in dataset_ids:
        glider_grab_copy.fetcher.dataset_id = dataset_id
        glider_df = _call_erddapy(glider_grab_copy)
        dataset_url = glider_grab_copy.fetcher.get_download_url().split("?")[0]
        glider_df = standardise_df(glider_df, dataset_url)
        df_all.update({dataset_id: glider_df})
    return df_all


def standardise_df(glider_df: pd.DataFrame, dataset_url: str) -> pd.DataFrame:
    """Standardise variable names in a dataset and add column for URL."""
    glider_df.columns = glider_df.columns.str.lower()
    glider_df = glider_df.set_index("time (utc)")
    glider_df = glider_df.rename(columns=server_parameter_rename)
    glider_df.index = pd.to_datetime(
        glider_df.index,
        format="%Y-%m-%dT%H:%M:%SZ",
    )
    # We need to sort b/c of the non-sequential submission of files due to
    # the nature of glider data transmission.
    glider_df = glider_df.sort_index()
    glider_df["dataset_url"] = dataset_url
    return glider_df


class GliderDataFetcher:
    """Instantiate the glider fetcher.

    Attributes
    ----------
    dataset_id : str
        A dataset unique id.
    constraints : dict
        Download constraints, defaults same as query.

    """

    def __init__(
        self: "GliderDataFetcher",
        server: OptionalStr = _server,
    ) -> None:
        """Instantiate main class attributes."""
        self.server = server
        self.fetcher = ERDDAP(
            server=server,
            protocol="tabledap",
        )
        self.fetcher.variables = server_vars[server]
        self.dataset_ids: OptionalList = None
        self.datasets: OptionalDF = None

    def to_pandas(self: "GliderDataFetcher") -> pd.DataFrame:
        """Return data from the server as a pandas dataframe.

        :return: pandas a dataframe with datetime UTC as index,
                 multiple dataset_ids dataframes are stored in a dictionary
        """
        if self.dataset_ids is not None:
            query = False  # Passing known dataset_ids
        elif self.dataset_ids is None and self.datasets is not None:
            query = True  # Passing an ERDDAP query
        else:
            msg = "Must provide a dataset_id or query terms to download data."
            raise ValueError(msg)

        glider_df = _to_pandas(self, query=query)
        # We need to reset to avoid fetching a single dataset_id when
        # making multiple requests.
        self.fetcher.dataset_id = None

        return glider_df

    def query(  # noqa: PLR0913
        self: "GliderDataFetcher",
        *,
        min_lat: OptionalNum = None,
        max_lat: OptionalNum = None,
        min_lon: OptionalNum = None,
        max_lon: OptionalNum = None,
        min_time: OptionalDateTime = None,
        max_time: OptionalDateTime = None,
        delayed: OptionalBool = False,
    ) -> pd.DataFrame:
        """Add user supplied geographical and time constraints to the query.

        :param min_lat: southernmost lat
        :param max_lat: northermost lat
        :param min_lon: westernmost lon (-180 to +180)
        :param max_lon: easternmost lon (-180 to +180)
        :param min_time: start time, can be datetime object or string
        :param max_time: end time, can be datetime object or string
        :return: search query with argument constraints applied
        """
        # NB: The time constrain could be better implemented by just
        # dropping it instead.
        min_time = min_time if min_time else "1970-01-01"
        max_time = max_time if max_time else "2038-01-19"
        min_lat = min_lat if min_lat else -90.0
        max_lat = max_lat if max_lat else 90.0
        min_lon = min_lon if min_lon else -180.0
        max_lon = max_lon if max_lon else 180.0

        self.fetcher.constraints = {
            "time>=": min_time,
            "time<=": max_time,
            "latitude>=": min_lat,
            "latitude<=": max_lat,
            "longitude>=": min_lon,
            "longitude<=": max_lon,
        }
        if self.datasets is None:
            url = self.fetcher.get_search_url(
                search_for="glider",
                response="csv",
                min_lat=min_lat,
                max_lat=max_lat,
                min_lon=min_lon,
                max_lon=max_lon,
                min_time=min_time,
                max_time=max_time,
            )
            self.query_url = url
            try:
                data = urlopen(url)
            except httpx.HTTPError as err:
                msg = (
                    "Error, no datasets found in supplied range. "
                    f"Try relaxing the constraints: {self.fetcher.constraints}"
                )
                err.message = f"{err.message}\n{msg}"
                raise

            cols = ["Title", "Institution", "Dataset ID"]
            datasets = pd.read_csv(data)[cols]
            if not delayed:
                datasets = datasets.loc[
                    ~datasets["Dataset ID"].str.endswith("delayed")
                ]
                info_urls = [
                    self.fetcher.get_info_url(
                        dataset_id=dataset_id,
                        response="html",
                    )
                    for dataset_id in datasets["Dataset ID"]
                ]
                datasets["info_url"] = info_urls
            self.datasets = datasets
        return self.datasets


class DatasetList:
    """Build a glider dataset ids list.


    Attributes
    ----------
      e : an ERDDAP server instance

    """

    def __init__(
        self: "DatasetList",
        *,
        server: OptionalStr = _server,
        search_for: OptionalStr = None,
        delayed: OptionalBool = False,
    ) -> None:
        """Instantiate main class attributes.

        Attributes
        ----------
        server : str
            the server URL.
        protocol : str
            ERDDAP's protocol (tabledap/griddap)
        search_for : str
            "Google-like" search of the datasets' metadata.

        """
        self.e = ERDDAP(
            server=server,
            protocol="tabledap",
        )
        self.search_for = search_for
        self.delayed = delayed

    def get_ids(self: "DatasetList") -> list:
        """Return the allDatasets list for the glider server."""
        if self.search_for is not None:
            url = self.e.get_search_url(
                search_for=self.search_for,
                response="csv",
            )
            dataset_ids = pd.read_csv(url)["Dataset ID"].to_list()
        else:
            self.e.dataset_id = "allDatasets"
            dataset_ids = self.e.to_pandas()["datasetID"].to_list()
            dataset_ids.remove("allDatasets")
        if not self.delayed:
            self.dataset_ids = [
                dataset_id
                for dataset_id in dataset_ids
                if not dataset_id.endswith("-delayed")
            ]
        return self.dataset_ids
