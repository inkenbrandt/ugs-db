#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
Programs.py
----------------------------------
the different source programs
'''

import csv
import pyodbc
import re
import schema
import sqlite3
import os
from collections import OrderedDict
from datetime import datetime
from dateutil.parser import parse as dateparser
from glob import glob
from os.path import join, isdir, basename, splitext
from querycsv import query_csv
from functools import partial
from services import Caster, Reproject, Normalizer, ChargeBalancer, HttpClient
from benchmarking import get_milliseconds


TEMPDB = 'temp.sqlite3'


class WqpProgram(object):
    '''class for handling wqp csv files'''

    datasource = 'WQP'

    wqp_url = ('http://www.waterqualitydata.us/{}/search?sampleMedia=Water&startDateLo={}&startDateHi={}&'
               'bBox=-115%2C35.5%2C-108%2C42.5&mimeType=csv')

    fields = {
        'sample_id': 'ActivityIdentifier',
        'monitoring_location_id': 'MonitoringLocationIdentifier'
    }

    sql = {
        'distinct_sample_id': 'select distinct({}) from {}',
        'sample_id': 'select * from {} where {} = \'{}\'',
        'wqxids': 'select {0} from {1} where {0} LIKE \'%_WQX%\'',
        'station_insert': ('insert into Stations (OrgId, OrgName, StationId, StationName, StationType, StationComment,'
                           + ' HUC8, Lon_X, Lat_Y, HorAcc, HorAccUnit, HorCollMeth, HorRef, Elev, ElevUnit, ElevAcc,'
                           + ' ElevAccUnit, ElevMeth, ElevRef, StateCode, CountyCode, Aquifer, FmType, AquiferType,'
                           + ' ConstDate, Depth, DepthUnit, HoleDepth, HoleDUnit, demELEVm, DataSource, WIN, Shape)'
                           + ' values ({})'),
        'result_insert': ('insert into Results (AnalysisDate, AnalytMeth, AnalytMethId, AutoQual, CAS_Reg, Chrg,'
                          + ' DataSource, DetectCond, IdNum, LabComments, LabName, Lat_Y, LimitType, Lon_X, MDL,'
                          + ' MDLUnit, MethodDescript, OrgId, OrgName, Param, ParamGroup, ProjectId, QualCode,'
                          + ' ResultComment, ResultStatus, ResultValue, SampComment, SampDepth, SampDepthRef,'
                          + ' SampDepthU, SampEquip, SampFrac, SampleDate, SampleTime, SampleId, SampMedia, SampMeth,'
                          + ' SampMethName, SampType, StationId, Unit, USGSPCode) values ({})'),
        'create_index': "CREATE INDEX IF NOT EXISTS 'ActivityIdentifier_{0}' ON '{0}' ('ActivityIdentifier' ASC)",
        'max_sample_date': 'SELECT max(SampleDate) FROM [UGSWaterChemistry].[dbo].[Results]',
        'new_stations': ('SELECT * FROM (VALUES{}) AS t(StationId) WHERE NOT EXISTS('
                         + 'SELECT 1 FROM [UGSWaterChemistry].[dbo].[Stations] WHERE [StationId] = t.StationId)'),
        'new_results': ('SELECT * FROM (VALUES{}) AS t(SampleId) WHERE NOT EXISTS('
                        + 'SELECT 1 FROM [UGSWaterChemistry].[dbo].[Results] WHERE [SampleId] = t.SampleId)')
    }

    wqx_re = re.compile('(_WQX)-')

    station_config = OrderedDict([
        ('OrganizationIdentifier', 'OrgId'),
        ('OrganizationFormalName', 'OrgName'),
        ('MonitoringLocationIdentifier', 'StationId'),
        ('MonitoringLocationName', 'StationName'),
        ('MonitoringLocationTypeName', 'StationType'),
        ('MonitoringLocationDescriptionText', 'StationComment'),
        ('HUCEightDigitCode', 'HUC8'),
        ('LongitudeMeasure', 'Lon_X'),
        ('LatitudeMeasure', 'Lat_Y'),
        ('HorizontalAccuracyMeasure/MeasureValue', 'HorAcc'),
        ('HorizontalAccuracyMeasure/MeasureUnitCode', 'HorAccUnit'),
        ('HorizontalCollectionMethodName', 'HorCollMeth'),
        ('HorizontalCoordinateReferenceSystemDatumName', 'HorRef'),
        ('VerticalMeasure/MeasureValue', 'Elev'),
        ('VerticalMeasure/MeasureUnitCode', 'ElevUnit'),
        ('VerticalAccuracyMeasure/MeasureValue', 'ElevAcc'),
        ('VerticalAccuracyMeasure/MeasureUnitCode', 'ElevAccUnit'),
        ('VerticalCollectionMethodName', 'ElevMeth'),
        ('VerticalCoordinateReferenceSystemDatumName', 'ElevRef'),
        ('StateCode', 'StateCode'),
        ('CountyCode', 'CountyCode'),
        ('AquiferName', 'Aquifer'),
        ('FormationTypeText', 'FmType'),
        ('AquiferTypeName', 'AquiferType'),
        ('ConstructionDateText', 'ConstDate'),
        ('WellDepthMeasure/MeasureValue', 'Depth'),
        ('WellDepthMeasure/MeasureUnitCode', 'DepthUnit'),
        ('WellHoleDepthMeasure/MeasureValue', 'HoleDepth'),
        ('WellHoleDepthMeasure/MeasureUnitCode', 'HoleDUnit'),
        ('demELEVm', '!demELEVm'),
        ('DataSource', '!DataSource'),
        ('WIN', '!WIN'),
        ('Shape', '!Shape')
    ])

    result_config = OrderedDict([
        ('AnalysisStartDate', 'AnalysisDate'),
        ('ResultAnalyticalMethod/MethodName', 'AnalytMeth'),
        ('ResultAnalyticalMethod/MethodIdentifier', 'AnalytMethId'),
        ('AutoQual', '!AutoQual'),
        ('CAS_Reg', '!CAS_Reg'),
        ('Chrg', '!Chrg'),
        ('DataSource', '!DataSource'),
        ('ResultDetectionConditionText', 'DetectCond'),
        ('IdNum', '!IdNum'),
        ('ResultLaboratoryCommentText', 'LabComments'),
        ('LaboratoryName', 'LabName'),
        ('Lat_Y', '!Lat_Y'),
        ('DetectionQuantitationLimitTypeName', 'LimitType'),
        ('Lon_X', '!Lon_X'),
        ('DetectionQuantitationLimitMeasure/MeasureValue', 'MDL'),
        ('DetectionQuantitationLimitMeasure/MeasureUnitCode', 'MDLUnit'),
        ('MethodDescriptionText', 'MethodDescript'),
        ('OrganizationIdentifier', 'OrgId'),
        ('OrganizationFormalName', 'OrgName'),
        ('CharacteristicName', 'Param'),
        ('ParamGroup', '!ParamGroup'),
        ('ProjectIdentifier', 'ProjectId'),
        ('MeasureQualifierCode', 'QualCode'),
        ('ResultCommentText', 'ResultComment'),
        ('ResultStatusIdentifier', 'ResultStatus'),
        ('ResultMeasureValue', 'ResultValue'),
        ('ActivityCommentText', 'SampComment'),
        ('ActivityDepthHeightMeasure/MeasureValue', 'SampDepth'),
        ('ActivityDepthAltitudeReferencePointText', 'SampDepthRef'),
        ('ActivityDepthHeightMeasure/MeasureUnitCode', 'SampDepthU'),
        ('SampleCollectionEquipmentName', 'SampEquip'),
        ('ResultSampleFractionText', 'SampFrac'),
        ('ActivityStartDate', 'SampleDate'),
        ('ActivityStartTime/Time', 'SampleTime'),
        ('ActivityIdentifier', 'SampleId'),
        ('ActivityMediaSubdivisionName', 'SampMedia'),
        ('SampleCollectionMethod/MethodIdentifier', 'SampMeth'),
        ('SampleCollectionMethod/MethodName', 'SampMethName'),
        ('ActivityTypeCode', 'SampType'),
        ('MonitoringLocationIdentifier', 'StationId'),
        ('ResultMeasure/MeasureUnitCode', 'Unit'),
        ('USGSPCode', 'USGSPCode')
    ])

    def __init__(self, db, file_location=None):
        '''create a new WQP program
        db - the connection string for the database to seed
        file_location - the path on disk to find csv files to ETL

        if `file_location` is None, it is assumed to be an update
        operation
        '''
        super(WqpProgram, self).__init__()

        self.db = db

        #: if file_location is None then we are updating
        if file_location is not None:
            #: check that file_location exists wqp/results and wqp/stations
            parent_folder = join(file_location, self.datasource)

            if not isdir(parent_folder):
                raise Exception('Pass in a location to the parent folder that contains WQP. {}'.format(parent_folder))

            results_folder = join(parent_folder, 'Results')
            stations_folder = join(parent_folder, 'Stations')

            if not isdir(results_folder or stations_folder):
                raise Exception('WQP folder is missing Results or Stations child folders containing csv files.')

            #: all is well set the folders for seeding later on
            self.results_folder = results_folder
            self.stations_folder = stations_folder

    def seed(self):
        if not hasattr(self, 'results_folder') or not hasattr(self, 'stations_folder'):
            raise Exception('You must pass a file location if you are seeding. Did you want to update?')

        try:
            self._seed_by_file()
        finally:
            if hasattr(self, 'cursor'):
                del self.cursor

    def update(self):
        try:
            last_updated = self._get_most_recent_result_date()

            if not last_updated:
                raise Exception('No last updated date')

            #: get new results from wqp service
            result_url = self._format_url(self.wqp_url, 'Result', last_updated)
            #: group them as if they were read from querycsv
            new_results = self._group_rows_by_id(HttpClient.get_csv(result_url))
            #: remove results that have a sample id already in the database
            new_results = self._remove_existing_results(new_results)
            #: find the station ids from the new results that aren't in the database
            new_station_ids = self._find_new_station_ids(new_results)
            #: check database for stripped wqx and remove wqx id's since they are duplicates
            new_station_ids = self._remove_existing_wqx_station_ids(new_station_ids)

            if new_station_ids and len(new_station_ids) > 0:
                print('of the new stations found, attempting to insert {}'.format(len(new_station_ids)))

                station_url = self._format_url(self.wqp_url, 'Station', last_updated)
                new_stations = HttpClient.get_csv(station_url)

                if not new_stations:
                    raise Exception('WQP service should have returned results but result is empty. {}'.format(station_url))

                header = new_stations.next()

                stations = self._extract_stations_by_id(new_stations, new_station_ids, header)
                wqx = self._get_wqx_duplicate_ids(stations)

                self._seed_stations(stations, header=header, wqx=wqx)
            else:
                print('all stations already in database')
            for samples_for_id in new_results.values():
                self._seed_results(samples_for_id)

        finally:
            if hasattr(self, 'cursor'):
                del self.cursor

    def _seed_by_file(self):
        print('processing stations')

        for csv_file in self._get_files(self.stations_folder):
            #: create csv reader
            with open(csv_file, 'r') as f:
                print('processing {}'.format(basename(csv_file)))

                #: generate duplicate id list
                wqx = self._get_wqx_duplicate_ids(csv_file)

                reader = csv.reader(f)
                #: get a reference to the header row
                header = reader.next()

                self._seed_stations(reader, header=header, wqx=wqx)

                print('processing {}: done'.format(basename(csv_file)))

        print('processing results')

        for csv_file in self._get_files(self.results_folder):
            print('processing {}'.format(basename(csv_file)))

            #: create sqlite db and get unique sample ids
            unique_sample_ids = self._get_distinct_sample_ids_from(csv_file)

            try:
                #: this needs to be done after get_distinct so that the table is created in the db
                self._add_sample_index(csv_file)

                sample_sets = 0
                sets_start = get_milliseconds()

                for sample_id in unique_sample_ids:
                    samples = self._get_samples_for_id(sample_id, csv_file)

                    self._seed_results(samples)

                    sample_sets += 1
                    elapsed = get_milliseconds() - sets_start
                    if sample_sets % 5000 == 0:
                        print('{} total sample sets (avg {} milliseconds per set)'.format(sample_sets,
                                                                                          round(elapsed/sample_sets, 5)))

            finally:
                #: in case something goes wrong always clean up the db
                os.remove(TEMPDB)
            print('processing {}: done'.format(basename(csv_file)))

    def _seed_stations(self, rows, header=None, wqx=None):
        stations = []

        for row in rows:
            #: push all the csv column names into the standard names
            row = self._etl_column_names(row, self.station_config, header=header)

            #: check for duplicate stationid and skip if found
            station_id = row['StationId']
            if not self.wqx_re.search(station_id) and station_id in wqx:
                continue

            #: cast
            row = Caster.cast(row, schema.station)

            #: set datasource, reproject and update shape
            row = self._update_row(row)

            #: normalize data including stripping _WXP etc
            row = Normalizer.normalize_station(row)

            #: reorder and filter out any fields not in the schema
            row = Normalizer.reorder_filter(row, schema.station)

            #: have to generate sql manually because of quoting on spatial WKT
            row = Caster.cast_for_sql(row)

            #: store row for later
            stations.append(row.values())

        #: insert stations
        self._insert_rows(stations, self.sql['station_insert'])

    def _seed_results(self, samples_for_id):
        #: cast to defined schema types
        samples = map(partial(Caster.cast, schema=schema.result), samples_for_id)

        #: set datasource and spatial information
        samples = map(self._update_row, samples)

        #: normalize chemical names and units
        samples = map(Normalizer.normalize_sample, samples)

        #: create charge balance rows from sample
        charge_balances = ChargeBalancer.get_charge_balance(samples)

        samples.extend(charge_balances)

        #: reorder and filter out any fields not in the schema
        samples = map(partial(Normalizer.reorder_filter, schema=schema.result), samples)

        samples = map(Caster.cast_for_sql, samples)

        rows = map(lambda sample: sample.values(), samples)

        #: TODO determine if this should this be batched in sets bigger than just a sample set?
        self._insert_rows(rows, self.sql['result_insert'])

    def _get_files(self, location):
        '''Takes the file location and returns the csv's within it.'''

        if not location:
            raise Exception('Pass in a location containing csv files to import.')

        files = glob(join(location, '*.csv'))

        if len(files) < 1:
            raise Exception(location, 'No csv files found.')

        return files

    def _get_distinct_sample_ids_from(self, file_path):
        '''Given a file_path, this returns a set of unique sample ids.'''

        file_name = self._get_file_name_without_extension(file_path)

        unique_sample_ids = query_csv(self.sql['distinct_sample_id'].format(self.fields['sample_id'], file_name),
                                      [file_path],
                                      TEMPDB)
        if len(unique_sample_ids) > 0:
            #: remove header cell
            unique_sample_ids.pop(0)

        return unique_sample_ids

    def _get_wqx_duplicate_ids(self, file_path):
        '''Given the file_path, return the list of stripped station ids that have the _WQX suffix'''

        file_name = None
        try:
            file_name = self._get_file_name_without_extension(file_path)
        except AttributeError:
            #: most likely list is not a string error
            #: we are sending in the updated stations
            stations = file_path

        if file_name:
            rows = query_csv(self.sql['wqxids'].format(self.fields['monitoring_location_id'], file_name),
                             [file_path],
                             TEMPDB)
            if len(rows) > 0:
                rows.pop(0)

            return set([re.sub(self.wqx_re, '-', row[0]) for row in rows])

        return set([re.sub(self.wqx_re, '-', station['StationId']) for station in filter(lambda x: self.wqx_re.search(x['StationId']), stations)])

    def _get_samples_for_id(self, sample_id_set, file_path, config=None):
        '''Given a `(id,)` styled set of sample ids, this will return the sample
        rows for the given csv file_path. The format will be an array of dictionaries
        with the key being the destination field name and the value being the source csv value.

        This is only invoked for result files.
        '''

        file_name = self._get_file_name_without_extension(file_path)
        samples_for_id = query_csv(self.sql['sample_id'].format(file_name, self.fields['sample_id'], sample_id_set[0]),
                                   [file_path],
                                   TEMPDB)

        return self._etl_column_names(samples_for_id, config or self.result_config)

    def _etl_column_names(self, rows, config, header=None):
        '''Given a dictionary or list of dictionaries, return a new row or
        list of rows with the correct field names'''

        #: we have an already etl'd station. skip it.
        if type(rows) is dict:
            return rows

        if len(rows) == 0:
            return None

        if not header:
            #: get header cell from rows and remove
            header = rows.pop(0)

        def return_value_if_not_in_config(key):
            if key in config:
                return config[key]

            return key

        header = map(lambda x: return_value_if_not_in_config(x), header)

        #: if we are passing a single item, not an array of sets, don't map over it.
        #: this is when we have a station and not a set of results
        if not isinstance(rows[0], tuple):
            return dict(zip(header, rows))

        return map(lambda x: dict(zip(header, x)), rows)

    def _get_file_name_without_extension(self, file_path):
        '''Given a filename with an extension, the file name is returned without the extension.'''

        return splitext(basename(file_path))[0]

    def _update_row(self, row):
        '''Given a dictionary as a row, take the lat and long field, project it to UTM, and transform to WKT'''

        template = 'geometry::STGeomFromText(\'POINT ({} {})\', 26912)'

        row['DataSource'] = self.datasource

        x = row['Lon_X']
        y = row['Lat_Y']

        if not (x and y):
            return row

        if 'Shape' not in row:
            return row

        shape = Reproject.to_utm(x, y)

        row['Shape'] = template.format(shape[0], shape[1])

        return row

    def _insert_rows(self, rows, insert_statement):
        '''Given a list of fields and an sql statement, execute the statement after `batch_size` number of statements'''
        batch_size = 5000

        if not hasattr(self, 'cursor') or not self.cursor:
            c = pyodbc.connect(self.db['connection_string'])
            self.cursor = c.cursor()

        i = 1
        #: format and stage sql statements
        for row in rows:
            statement = insert_statement.format(','.join(row))

            try:
                self.cursor.execute(statement)
                i += 1
            except Exception, e:
                del self.cursor
                raise e

            #: commit commands to database
            if i % batch_size == 0:
                self.cursor.commit()

            self.cursor.commit()

    def _add_sample_index(self, filepath):
        '''Add an index to ActivityIdentifier field for the table matching the file'''
        with sqlite3.connect(TEMPDB) as conn:
            conn.cursor().execute(self.sql['create_index'].format(basename(filepath)[:-4]))

    def _get_most_recent_result_date(self):
        #: open connection if one hasn't been opened
        if not hasattr(self, 'cursor') or not self.cursor:
            c = pyodbc.connect(self.db['connection_string'])
            self.cursor = c.cursor()

        try:
            last_updated = self.cursor.execute(self.sql['max_sample_date']).fetchone()
        except Exception, e:
            del self.cursor
            raise e

        #: fetchone returns a set with one item
        if last_updated and len(last_updated) == 1:
            return last_updated[0]

    def _format_url(self, template, source, last_updated, today=None):
        date_format = '%m-%d-%Y'
        lo = dateparser(last_updated).strftime(date_format)
        hi = datetime.now().strftime(date_format)

        if today:
            hi = dateparser(today).strftime(date_format)

        return template.format(source, lo, hi)

    def _group_rows_by_id(self, cursor, config=None):
        '''groups samples by SampleId as they would be formatted by querycsv
        cursor: generator
        config: an optional config to setup the etl. mainly for testing

        returns a dicionary with sample_id's as the key, with a list of rows as values
        '''
        unique_sample_ids = {}

        if not cursor:
            return

        header = cursor.next()

        for row in cursor:
            row = self._etl_column_names(row, config or self.result_config, header=header)

            sample_id = row['SampleId']
            if sample_id in unique_sample_ids:
                unique_sample_ids[sample_id] += [row]
            else:
                unique_sample_ids[sample_id] = [row]

        return unique_sample_ids

    def _find_new_station_ids(self, rows):
        '''gets the station ids to process from the rows
        rows: {sampleId: list(results)} the sorted results from an update operation

        returns a set of station ids
        '''

        if not rows or len(rows) < 1:
            return []

        unique_station_ids = set([])

        for results in rows.values():
            for result in results:
                station_id = result['StationId']

                unique_station_ids.add(station_id)

        unique_station_ids = self._get_unique_station_ids(list(unique_station_ids))

        return [id[0] for id in unique_station_ids]

    def _get_unique_station_ids(self, station_ids):
        '''queries the Stations table to find stations that have not been inserted yet
        stations_ids: list('StationId')

        returns a set of station ids
        '''

        station_ids = map(lambda station_id: '(\'{}\')'.format(station_id), station_ids)

        if not hasattr(self, 'cursor') or not self.cursor:
            c = pyodbc.connect(self.db['connection_string'])
            self.cursor = c.cursor()

        statement = self.sql['new_stations'].format(','.join(station_ids))
        self.cursor.execute(statement)

        return self.cursor.fetchall()

    def _extract_stations_by_id(self, cursor, station_ids, header):
        '''loops over a cursor of stations and returns the stations that have an id
        in station_ids
        cursor: a generator of stations. they can be ETL'd or not
        station_ids: list(stationids)
        header: list(column names)

        returns a list of stations
        '''
        if not cursor:
            return

        stations_to_insert = []

        for row in cursor:
            #: have to etl row to check station id
            row = self._etl_column_names(row, self.station_config, header=header)

            if row['StationId'] in station_ids:
                stations_to_insert.append(row)

        return stations_to_insert

    def _remove_existing_wqx_station_ids(self, station_ids):
        '''takes a list of station ids and finds the wqx ids. It strips the wqx
        and then queries the database to see if the stripped id is already in the db.
        Removing any station id's that are already in the db.
        station_ids: list('station_id')

        returns a list of station_ids
        '''
        #: pull out wqx ids from Stations
        wqxs = set([id for id in filter(lambda x: self.wqx_re.search(x), station_ids)])

        #: if there are no wqx's then return
        if len(wqxs) < 1:
            return station_ids

        #: remove the wqx because the database does not store that value
        stripped_wqx = [re.sub(self.wqx_re, '-', id) for id in wqxs]

        #: get back the unique id's from the wqx subset
        uniques = set([stripped[0] for stripped in self._get_unique_station_ids(stripped_wqx)])

        #: remove the ids that are not in unique
        for id in station_ids:
            if re.sub(self.wqx_re, '-', id) not in uniques:
                station_ids.remove(id)

        return station_ids

    def _remove_existing_results(self, results):
        sample_ids = map(lambda sample_id: '(\'{}\')'.format(sample_id), results.keys())

        unique_sample_ids = self._get_unique_sample_ids(sample_ids)
        #: flatten list
        unique_sample_ids = [item for iter_ in unique_sample_ids for item in iter_]

        return {key: results[key] for key in results if key in unique_sample_ids}

    def _get_unique_sample_ids(self, sample_ids):
        if not hasattr(self, 'cursor') or not self.cursor:
            c = pyodbc.connect(self.db['connection_string'])
            self.cursor = c.cursor()

        statement = self.sql['new_results'].format(','.join(sample_ids))
        self.cursor.execute(statement)

        return self.cursor.fetchall()
