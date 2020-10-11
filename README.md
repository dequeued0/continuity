# Continuity

Continuity is a replacement for the AutoModerator schedule on Reddit. Continuity runs as a command line script and the easiest way to set it up is as an hourly cron job.

## Features

- Continuity allows Reddit scheduled posts to be configured via a wiki page. The format is the same as the [`automoderator-schedule` format](https://www.reddit.com/r/AutoModerator/comments/1z7rlu/now_available_for_testing_wikiconfigurable/) used by AutoModerator and it can even be left in the same location.
- Supports all of the features from AutoModerator schedule including:
  - including dates in title and text
  - recurrence rules (`rrule`)
- Includes two testing modes: one for dry runs and one for posting to a sandbox subreddit.

## Setup

### Python packages

Requires praw, PyYAML, python-dateutil, and regex.

### Notes

If you want to leave the wiki page in the same location as AutoModerator, make sure you disable AutoModerator by saving an empty configuration and sending /u/AutoModerator a private message to tell it to update (this may require a large number of attempts). After AutoModerator has sent an acknowledgement, you can restore your working configuration.

## Examples

- `mybotname` is the name of a section in your praw.ini file
- `mysubreddit` is the name of your subreddit

### Dry-run test

    $ continuity.py --brief --dry-run --start=20201201T000000Z --end=20210101T000000Z mybotname automoderator-schedule mysubreddit
    2020-10-11T05:16:31 | INFO | submit_queue | 2020-12-04T17:00:00-04:00: Weekend Discussion and Victory Thread for the week of December 04, 2020
    2020-10-11T05:16:32 | INFO | submit_queue | 2020-12-07T07:00:00-04:00: Weekday Help and Victory Thread for the week of December 07, 2020
    2020-10-11T05:16:33 | INFO | submit_queue | 2020-12-11T17:00:00-04:00: Weekend Discussion and Victory Thread for the week of December 11, 2020
    2020-10-11T05:16:33 | INFO | submit_queue | 2020-12-14T07:00:00-04:00: Weekday Help and Victory Thread for the week of December 14, 2020
    2020-10-11T05:16:34 | INFO | submit_queue | 2020-12-18T17:00:00-04:00: Weekend Discussion and Victory Thread for the week of December 18, 2020
    2020-10-11T05:16:34 | INFO | submit_queue | 2020-12-21T07:00:00-04:00: Weekday Help and Victory Thread for the week of December 21, 2020
    2020-10-11T05:16:35 | INFO | submit_queue | 2020-12-25T17:00:00-04:00: Weekend Discussion and Victory Thread for the week of December 25, 2020
    2020-10-11T05:16:36 | INFO | submit_queue | 2020-12-27T09:00:00-04:00: What are your 2021 financial goals?
    2020-10-11T05:16:36 | INFO | submit_queue | 2020-12-28T07:00:00-04:00: Weekday Help and Victory Thread for the week of December 28, 2020

### Linux cron job configuration

    @hourly /home/myusername/scripts/continuity.py mybotname automoderator-schedule mysubreddit
