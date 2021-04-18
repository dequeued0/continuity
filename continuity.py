#!/usr/bin/env python3.7
'''
Continuity
'''

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
import praw
import prawcore.exceptions
import regex
import yaml
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrulestr

# setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(funcName)s | %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)

# arguments
parser = argparse.ArgumentParser(
    description='''This is a replacement for the AutoModerator schedule. This is
    designed to run as a cron job (default: an hourly cron job, easiest usage if
    posts are always scheduled for the top of the hour).'''
)
parser.add_argument('--start', type=str, help='end timestamp for tests')
parser.add_argument('--end', type=str, help='start timestamp for tests')
parser.add_argument('--sandbox', type=str, help='redirect posts to /r/SANDBOX')
parser.add_argument('--dry-run', action='store_true', help='do not post, only show potential posts')
parser.add_argument('--brief', action='store_true', help='brief output format for dry runs')
parser.add_argument('--limit', type=int, default=2,
                    help='post limit per subreddit per run (default: 2)')
parser.add_argument('--seconds', type=int, default=3540,
                    help='''submit posts scheduled within the last SECONDS seconds (default: 3540),
                    do not change this unless you are trying to change away from hourly cron jobs
                    and you know what you are doing''')
parser.add_argument('configuration', type=str, help='PRAW configuration section')
parser.add_argument('wiki', type=str, help='name of wiki page to use')
parser.add_argument('subreddit', action='store', type=str, nargs='+', help='name of subreddit')
args = parser.parse_args()

# reddit
r = praw.Reddit(args.configuration)
r.validate_on_submit = True


def read_schedule(subreddit):
    '''
    reads a wiki page (traditionally automoderator-schedule) schedule
    :param subreddit: praw subreddit
    :return: posts to be made
    '''
    posts = []

    for attempt in range(5):
        try:
            page = subreddit.wiki[args.wiki]
            if len(page.content_md) > 0:
                for section in yaml.safe_load_all(page.content_md):
                    post = process_section(subreddit, section)
                    if post:
                        posts.append(post)

            # success
            if posts:
                return posts

            # no posts and no exceptions?
            logging.error(f"empty schedule for /r/{subreddit}")
            break
        # unrecoverable exception
        except (prawcore.exceptions.Forbidden, prawcore.exceptions.NotFound) as error:
            logging.error(f"unable to read schedule on /r/{subreddit}: {error}")
            break
        # possibly recoverable exception
        except Exception as error:
            logging.error(f"exception reading schedule on /r/{subreddit}: {error}")

        # always sleep after failures
        delay = (attempt + 1) * 30
        logging.info(f"sleeping {delay} seconds")
        time.sleep(delay)

    # only reached on failure
    subreddit.message("Scheduled post issue", "Unable to read schedule. Please investigate.")
    return []

def process_section(subreddit, section):
    '''
    processes a specific of the schedule and generates a post for that section
    :param subreddit: praw subreddit
    :param section: a specific yaml section of the schedule
    :return:post to be made or None
    '''
    post = {}

    try:
        if not section:
            return None
        # subreddit
        if args.sandbox:
            post["subreddit"] = r.subreddit(args.sandbox)
        else:
            post["subreddit"] = subreddit
        # skip if missing required settings
        if not (section.get("title") and section.get("text") and section.get("first")):
            return None
        # simple settings that just default to None
        for field in ["title", "text", "rrule", "contest_mode"]:
            post[field] = section.get(field)
        # more complex settings
        post["first"] = parse(section.get("first"))
        post["distinguish"] = section.get("distinguish", True)
        if section.get("repeat"):
            match = regex.search(r'\b(\d+)\s+(hour|day|week|month|year)s?\b', section.get("repeat"))
            if match:
                if match.group(2) == "hour":
                    post["repeat"] = relativedelta(hours=int(match.group(1)))
                elif match.group(2) == "day":
                    post["repeat"] = relativedelta(days=int(match.group(1)))
                elif match.group(2) == "week":
                    post["repeat"] = relativedelta(weeks=int(match.group(1)))
                elif match.group(2) == "month":
                    post["repeat"] = relativedelta(months=int(match.group(1)))
                elif match.group(2) == "year":
                    post["repeat"] = relativedelta(years=int(match.group(1)))
        if section.get("sticky"):
            if regex.search(r'^1$', str(section.get("sticky"))):
                post["sticky"] = 1
            elif regex.search(r'^(2|true)$', str(section.get("sticky"))):
                post["sticky"] = 2
    except Exception as error:
        logging.error(f"exception reading {section} on /r/{subreddit}: {error}")
        return None

    return post


def consider_posts(posts, now):
    '''
    considers posts to be made and adds them to a queue

    :param posts: posts to be consider to be made
    :param now: starting time for posts to be considered to be made
    :return: queue of posts to be made
    '''
    queue = []

    for post in posts:
        try:
            rrule = None
            if post.get("rrule"):
                rrulestring = post.get("rrule")
                if not regex.search(r'(^|;)UNTIL=', rrulestring):
                    rrulestring += replace_dates(";UNTIL={{date+1 %Y%m%dT%H%M%SZ}}", now)
                rrule = list(rrulestr(rrulestring, dtstart=post["first"]))
                if rrule:
                    current = rrule.pop(0)
                else:
                    continue
            else:
                current = post["first"]

            current = current.replace(tzinfo=timezone.utc)

            while current <= now:
                if (now - current).total_seconds() < args.seconds:
                    title = replace_dates(post["title"], now)
                    text = replace_dates(post["text"], now)
                    queue.append({"subreddit": post.get("subreddit"),
                                  "title": title, "text": text,
                                  "when": current.isoformat(),
                                  "sticky": post.get("sticky"),
                                  "distinguish": post.get("distinguish"),
                                  "contest_mode": post.get("contest_mode")})
                if post.get("rrule"):
                    if rrule:
                        current = rrule.pop(0)
                    else:
                        break
                elif post.get("repeat"):
                    current += post.get("repeat")
                else:
                    break
        except Exception as error:
            logging.error(f"exception considering {post}: {error}")

    return queue


def replace_dates(string, now):
    '''

    :param string:
    :param now:
    :return:
    '''
    count = 0

    while count < 100:
        count += 1
        match = regex.search(r'\{\{date(?:([+-])(\d+))?\s+(.*?)\}\}', string)
        if not match:
            break
        output_date = now
        if match.group(1) == "+":
            output_date += relativedelta(days=int(match.group(2)))
        elif match.group(1) == "-":
            output_date -= relativedelta(days=int(match.group(2)))
        timeformat = output_date.strftime(match.group(3))
        string = string[:match.start()] + timeformat + string[match.end():]

    return string


def submit_queue(queue):
    '''
    Submits the queue of posts to the subreddit
    :param queue:
    :return: None
    '''
    post = None
    try:
        if not queue:
            return
        if len(queue) > args.limit:
            logging.error(f"submission queue is {len(queue)} entries long, something may be wrong")
            subreddits = set()
            for post in queue:
                if post.get("subreddit"):
                    subreddits.add(post["subreddit"])
            for subreddit in subreddits:
                subreddit.message(f"Scheduled post issue",
                                  "Attempted to make {len(queue)} posts at once. Please investigate.")
            sys.exit(1)
    except Exception as error:
        logging.error(f"exception checking post limit {queue}: {error}")
        sys.exit(1)

    try:
        for post in queue:
            submit_post(post)

    except Exception as error:
        logging.error(f"exception posting queue {queue}: {error}")
        sys.exit(1)


def recently_exists(subreddit, title):
    '''
    checks if a post with the same title recently exists
    :param subreddit: praw subreddit
    :param title: title of a reddit post
    :return: reddit post or False
    '''
    for recent in r.user.me().submissions.new(limit=100):
        if recent.created_utc < time.time() - args.seconds:
            continue
        if recent.subreddit == subreddit and recent.title == title:
            return recent
    return False


def submit_post(post):
    '''
    submits a post to reddit
    :param post: post to be submitted
    :return: None
    '''
    submission = None
    distinguish = False
    sticky = False
    contest_mode = False
    disable_inbox_replies = False

    for attempt in range(5):
        try:
            if args.brief:
                logging.info(f"{post['when']}: {post['title']}")
            else:
                logging.info(f"posting {post}")

            # dry-run mode
            if args.dry_run:
                return

            # avoid posting more than once
            if attempt > 0:
                if not submission:
                    submission = recently_exists(post["subreddit"], post["title"])
                if submission:
                    logging.info(f"already submitted {submission.permalink}")

            # do stuff
            if not submission:
                submission = post["subreddit"].submit(post["title"], selftext=post["text"])
            if post.get("distinguish") and not distinguish:
                submission.mod.distinguish()
                distinguish = True
            if post.get("sticky") and not sticky:
                if post["sticky"] == 1:
                    submission.mod.sticky(bottom=False)
                else:
                    submission.mod.sticky(bottom=True)
                sticky = True
            if post.get("contest_mode") and not contest_mode:
                submission.contest_mode()
                contest_mode = True
            if not disable_inbox_replies:
                submission.disable_inbox_replies()
                disable_inbox_replies = True

            # sandbox test mode
            if args.sandbox:
                submission.reply(post["when"])

            # success
            if submission:
                return

            # no submission and no exceptions?
            logging.error(f"unknown error making post {post}")
        except Exception as error:
            logging.error(f"exception making post {post}: {error}")

        # always sleep after failures
        delay = (attempt + 1) * 30
        logging.info(f"sleeping {delay} seconds")
        time.sleep(delay)

    # only reached on failure
    if post.get("subreddit"):
        post["subreddit"].message("Scheduled post issue", "Please investigate.")


def run():
    '''
    runs continuity
    :return: None
    '''
    posts = []

    # read schedules
    for subreddit in args.subreddit:
        schedule = read_schedule(r.subreddit(subreddit))
        if schedule:
            posts.extend(schedule)

    # test mode
    if args.start or args.end or args.sandbox or args.dry_run:
        if args.sandbox == args.dry_run:
            logging.error("set either --sandbox SANDBOX or --dry-run when testing")
            sys.exit(1)
        if not args.start or not args.end:
            logging.error("set both --start START and --end END when testing")
            sys.exit(1)
        start = parse(args.start)
        end = parse(args.end)
        if parse("19700101T000000Z") <= start < end <= parse("20700101T000000Z"):
            now = start
            while now <= end:
                queue = consider_posts(posts, now)
                submit_queue(queue)
                now += relativedelta(hours=1)
        else:
            logging.error(f"invalid timestamps (start={start}, end={end})")
            sys.exit(1)
        sys.exit(0)

    # not a test
    now = datetime.now(timezone.utc)
    queue = consider_posts(posts, now)
    submit_queue(queue)
    sys.exit(0)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        logging.error("received SIGINT from keyboard, stopping")
        sys.exit(1)
    except Exception as error:
        logging.error(f"site error: {error}")
        sys.exit(1)
