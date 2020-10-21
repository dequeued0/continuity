#!/usr/bin/env python3.7

import sys
import logging
import argparse
import praw
import prawcore.exceptions
import regex
import time
import yaml
from datetime import datetime, timezone
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
parser.add_argument('--limit', type=int, default=2, help='post limit per subreddit per run (default: 2)')
parser.add_argument('--seconds', type=int, default=3540, help='submit posts scheduled within the last SECONDS seconds (default: 3540), do not change this unless you are trying to change away from hourly cron jobs and you know what you are doing')
parser.add_argument('configuration', type=str, help='PRAW configuration section')
parser.add_argument('wiki', type=str, help='name of wiki page to use')
parser.add_argument('subreddit', action='store', type=str, nargs='+', help='name of subreddit')
args = parser.parse_args()

# reddit
r = praw.Reddit(args.configuration)
r.validate_on_submit = True


def read_schedule(subreddit):
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
            logging.error("empty schedule for /r/{}".format(subreddit))
            break
        # unrecoverable exception
        except (prawcore.exceptions.Forbidden, prawcore.exceptions.NotFound) as e:
            logging.error("unable to read schedule on /r/{}: {}".format(subreddit, e))
            break
        # possibly recoverable exception
        except Exception as e:
            logging.error("exception reading schedule on /r/{}: {}".format(subreddit, e))

        # always sleep after failures
        delay = (attempt + 1) * 30
        logging.info("sleeping {} seconds".format(delay))
        time.sleep(delay)

    # only reached on failure
    subreddit.message("Scheduled post issue", "Unable to read schedule. Please investigate.")


def process_section(subreddit, section):
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
            m = regex.search(r'\b(\d+)\s+(hour|day|week|month|year)s?\b', section.get("repeat"))
            if m:
                if m.group(2) == "hour":
                    post["repeat"] = relativedelta(hours=int(m.group(1)))
                elif m.group(2) == "day":
                    post["repeat"] = relativedelta(days=int(m.group(1)))
                elif m.group(2) == "week":
                    post["repeat"] = relativedelta(weeks=int(m.group(1)))
                elif m.group(2) == "month":
                    post["repeat"] = relativedelta(months=int(m.group(1)))
                elif m.group(2) == "year":
                    post["repeat"] = relativedelta(years=int(m.group(1)))
        if section.get("sticky"):
            if regex.search(r'^1$', str(section.get("sticky"))):
                post["sticky"] = 1
            elif regex.search(r'^(2|true)$', str(section.get("sticky"))):
                post["sticky"] = 2
    except Exception as e:
        logging.error("exception reading {} on /r/{}: {}".format(section, subreddit, e))
        return None

    return post


def consider_posts(posts, now):
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
            while current <= now:
                if (now - current).total_seconds() < args.seconds:
                    title = replace_dates(post["title"], now)
                    text = replace_dates(post["text"], now)
                    queue.append({ "subreddit":post.get("subreddit"),
                                   "title":title, "text":text,
                                   "when":current.isoformat(),
                                   "sticky":post.get("sticky"),
                                   "distinguish":post.get("distinguish"),
                                   "contest_mode":post.get("contest_mode") })
                if post.get("rrule"):
                    if rrule:
                        current = rrule.pop(0)
                    else:
                        break
                elif post.get("repeat"):
                    current += post.get("repeat")
                else:
                    break
        except Exception as e:
            logging.error("exception considering {}: {}".format(post, e))

    return queue


def replace_dates(string, now):
    count = 0

    while count < 100:
        count += 1
        m = regex.search(r'\{\{date(?:([+-])(\d+))?\s+(.*?)\}\}', string)
        if not m:
            break
        output_date = now
        if m.group(1) == "+":
            output_date += relativedelta(days=int(m.group(2)))
        elif m.group(1) == "-":
            output_date -= relativedelta(days=int(m.group(2)))
        timeformat = output_date.strftime(m.group(3))
        string = string[:m.start()] + timeformat + string[m.end():]

    return string


def submit_queue(queue):
    post = None

    try:
        if not queue:
            return
        if len(queue) > args.limit:
            logging.error("submission queue is {} entries long, something may be wrong".format(len(queue)))
            subreddits = set()
            for post in queue:
                if post.get("subreddit"):
                    subreddits.add(post["subreddit"])
            for subreddit in subreddits:
                subreddit.message("Scheduled post issue",
                                  "Attempted to make {} posts at once. Please investigate.".format(len(queue)))
            sys.exit(1)
    except Exception as e:
        logging.error("exception checking post limit {}: {}".format(queue, e))
        sys.exit(1)

    try:
        for post in queue:
            submit_post(post)
    except Exception as e:
        logging.error("exception posting queue {}: {}".format(queue, e))
        sys.exit(1)


def recently_exists(subreddit, title):
    for recent in r.user.me().submissions.new(limit=100):
        if recent.created_utc < time.time() - args.seconds:
            continue
        if recent.subreddit == subreddit and recent.title == title:
            return recent
    return False


def submit_post(post):
    submission = None
    distinguish = False
    sticky = False
    contest_mode = False
    disable_inbox_replies = False

    for attempt in range(5):
        try:
            if args.brief:
                logging.info("{}: {}".format(post["when"], post["title"]))
            else:
                logging.info("posting {}".format(post))

            # dry-run mode
            if args.dry_run:
                return

            # avoid posting more than once
            if attempt > 0:
                if not submission:
                    submission = recently_exists(post["subreddit"], post["title"])
                if submission:
                    logging.info("already submitted {}".format(submission.permalink))

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
            logging.error("unknown error making post {}".format(post))
        except Exception as e:
            logging.error("exception making post {}: {}".format(post, e))

        # always sleep after failures
        delay = (attempt + 1) * 30
        logging.info("sleeping {} seconds".format(delay))
        time.sleep(delay)

    # only reached on failure
    if post.get("subreddit"):
        post["subreddit"].message("Scheduled post issue", "Please investigate.")


def run():
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
        if start < end and start >= parse("19700101T000000Z") and end <= parse("20700101T000000Z"):
            now = start
            while now <= end:
                queue = consider_posts(posts, now)
                submit_queue(queue)
                now += relativedelta(hours=1)
        else:
            logging.error("invalid timestamps (start={}, end={})".format(start, end))
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
    except Exception as e:
        logging.error("site error: " + str(e))
        sys.exit(1)
