from lib.stravaApi import get_strava_client, strava_connected
from lib.ouraAPI import pull_oura_data
from lib.withingsAPI import pull_withings_data
from lib.fitbodAPI import pull_fitbod_data
from lib.sqlalchemy_declarative import *
from sqlalchemy import func, delete
import datetime
from lib.fitlyAPI import *
import pandas as pd
import configparser
from dash_app import dash_app

config = configparser.ConfigParser()
config.read('./config.ini')



def latest_refresh():
    session, engine = db_connect()
    latest_date = session.query(func.max(dbRefreshStatus.timestamp_utc))[0][0]
    engine.dispose()
    session.close()
    return latest_date


def refresh_database(process='system', truncate=False, truncateDate=None):
    # If either truncate parameter is passed
    if truncate or truncateDate:
        session, engine = db_connect()
        # If only truncating past a certain date
        if truncateDate:
            try:
                dash_app.server.logger.debug('Truncating strava_summary')
                session.execute(delete(stravaSummary).where(stravaSummary.start_date_utc >= truncateDate))
                dash_app.server.logger.debug('Truncating strava_samples')
                session.execute(delete(stravaSamples).where(stravaSamples.timestamp_local >= truncateDate))
                dash_app.server.logger.debug('Truncating strava_best_samples')
                session.execute(delete(stravaBestSamples).where(stravaBestSamples.timestamp_local >= truncateDate))
                dash_app.server.logger.debug('Truncating oura_readiness_summary')
                session.execute(delete(ouraReadinessSummary).where(ouraReadinessSummary.report_date >= truncateDate))
                dash_app.server.logger.debug('Truncating oura_sleep_summary')
                session.execute(delete(ouraSleepSummary).where(ouraSleepSummary.report_date >= truncateDate))
                dash_app.server.logger.debug('Truncating oura_sleep_samples')
                session.execute(delete(ouraSleepSamples).where(ouraSleepSamples.report_date >= truncateDate))
                dash_app.server.logger.debug('Truncating oura_activity_summary')
                session.execute(delete(ouraActivitySummary).where(ouraActivitySummary.summary_date >= truncateDate))
                dash_app.server.logger.debug('Truncating oura_activity_samples')
                session.execute(delete(ouraActivitySamples).where(ouraActivitySamples.timestamp_local >= truncateDate))
                dash_app.server.logger.debug('Truncating hrv_workout_step_log')
                session.execute(delete(hrvWorkoutStepLog).where(hrvWorkoutStepLog.date >= truncateDate))
                dash_app.server.logger.debug('Truncating withings')
                session.execute(delete(withings).where(withings.date_utc >= truncateDate))
                session.commit()
            except BaseException as e:
                session.rollback()
                dash_app.server.logger.error(e)
        else:
            try:
                dash_app.server.logger.debug('Truncating strava_summary')
                session.execute(delete(stravaSummary))
                dash_app.server.logger.debug('Truncating strava_samples')
                session.execute(delete(stravaSamples))
                dash_app.server.logger.debug('Truncating strava_best_samples')
                session.execute(delete(stravaBestSamples))
                dash_app.server.logger.debug('Truncating oura_readiness_summary')
                session.execute(delete(ouraReadinessSummary))
                dash_app.server.logger.debug('Truncating oura_sleep_summary')
                session.execute(delete(ouraSleepSummary))
                dash_app.server.logger.debug('Truncating oura_sleep_samples')
                session.execute(delete(ouraSleepSamples))
                dash_app.server.logger.debug('Truncating oura_activity_summary')
                session.execute(delete(ouraActivitySummary))
                dash_app.server.logger.debug('Truncating oura_activity_samples')
                session.execute(delete(ouraActivitySamples))
                dash_app.server.logger.debug('Truncating hrv_workout_step_log')
                session.execute(delete(hrvWorkoutStepLog))
                dash_app.server.logger.debug('Truncating withings')
                session.execute(delete(withings))
                session.commit()
            except BaseException as e:
                session.rollback()
                dash_app.server.logger.error(e)

        engine.dispose()
        session.close()

    # Pull Weight Data
    try:
        dash_app.server.logger.info('Pulling withings data...')
        pull_withings_data()
        withings_status = 'Successful'
    except BaseException as e:
        dash_app.server.logger.error('Error pulling withings data: {}'.format(e))
        withings_status = e

    # Pull Fitbod Data
    try:
        dash_app.server.logger.info('Pulling fitbod data...')
        pull_fitbod_data()
        fitbod_status = 'Successful'
    except BaseException as e:
        dash_app.server.logger.error('Error pulling withings data: {}'.format(e))
        fitbod_status = e

    # Pull Oura Data before strava because resting heart rate used in strava sample heart rate zones
    try:
        dash_app.server.logger.info('Pulling oura data...')
        oura_status = pull_oura_data()
        oura_status = 'Successful' if oura_status else 'Oura cloud not yet updated'
    except BaseException as e:
        dash_app.server.logger.error('Error pulling oura data: {}'.format(e))
        oura_status = e

    # Only pull strava data if oura cloud has been updated with latest day

    # Pull Strava Data
    if oura_status == 'Successful':
        try:
            dash_app.server.logger.info('Pulling strava data...')

            if strava_connected():
                athlete_id = 1  # TODO: Make this dynamic if ever expanding to more users
                client = get_strava_client()
                after = config.get('strava', 'activities_after_date')
                activities = client.get_activities(after=after, limit=0)  # Use after to sort from oldest to newest
                session, engine = db_connect()
                athlete_info = session.query(athlete).filter(athlete.athlete_id == athlete_id).first()
                min_non_warmup_workout_time = athlete_info.min_non_warmup_workout_time
                # Loop through the activities, and create a dict of the dataframe stream data of each activity
                db_activities = pd.read_sql(
                    sql=session.query(stravaSummary.activity_id).filter(
                        stravaSummary.athlete_id == athlete_id).distinct(stravaSummary.activity_id).statement,
                    con=engine)
                engine.dispose()
                session.close()
                new_activities = []
                for act in activities:
                    # If not already in db, parse and insert
                    if act.id not in db_activities['activity_id'].unique():
                        new_activities.append(FitlyActivity(act))
                        dash_app.server.logger.info('New Workout found: "{}"'.format(act.name))
                # If new workouts found, analyze and insert
                if len(new_activities) > 0:
                    for fitly_act in new_activities:
                        fitly_act.stravaScrape(athlete_id=athlete_id)
                hrv_training_workflow(min_non_warmup_workout_time=min_non_warmup_workout_time)

            dash_app.server.logger.debug('stravaScrape() complete...')
            strava_status = 'Successful'
        except BaseException as e:
            dash_app.server.logger.error('Error pulling strava data: {}'.format(e))
            strava_status = e
    else:
        dash_app.server.logger.info('Oura cloud not yet updated. Waiting to pull Strava data')
        strava_status = 'Awaiting oura cloud update'

    session, engine = db_connect()
    run_time = datetime.utcnow()
    record = dbRefreshStatus(timestamp_utc=datetime.utcnow(), oura_status=oura_status,
                             fitbod_status=fitbod_status,
                             strava_status=strava_status, withings_status=withings_status, truncate=truncate,
                             process=process)
    # Insert and commit
    try:
        session.add(record)
        session.commit()
        dash_app.server.logger.info('Inserting records into db_refresh...')
    except BaseException as e:
        session.rollback()
        print('Failed to insert db refresh status:', str(e))
        dash_app.server.logger.error(e)

    dash_app.server.logger.info('Refresh Complete')

    engine.dispose()
    session.close()

    return run_time
