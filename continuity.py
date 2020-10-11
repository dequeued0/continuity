#!/usr/bin/env python3.7

import sys
import logging
import argparse
import praw
import regex
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
    try:
        page = subreddit.wiki[args.wiki]
        if len(page.content_md) > 0:
            for section in yaml.safe_load_all(page.content_md):
                try:
                    post = {}
                    if not section:
                        continue
                    # subreddit
                    if args.sandbox:
                        post["subreddit"] = r.subreddit(args.sandbox)
                    else:
                        post["subreddit"] = subreddit
                    # skip if missing required settings
                    if not (section.get("title") and section.get("text") and section.get("first")):
                        continue
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
                    # save the post
                    posts.append(post)
                except Exception as e:
                    logging.error("exception reading {} for {}: {}".format(section, subreddit, e))
    except Exception as e:
        logging.debug("exception reading schedule for {}: {}".format(subreddit, e))
    return posts


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
    if not queue:
        return
    post = None
    try:
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
        else:
            for post in queue:
                if args.brief:
                    logging.info("{}: {}".format(post["when"], post["title"]))
                else:
                    logging.info("posting {}".format(post))
                if args.dry_run:
                    continue
                submission = post["subreddit"].submit(post["title"], selftext=post["text"])
                if post.get("distinguish"):
                    submission.mod.distinguish()
                if post.get("sticky"):
                    if post["sticky"] == 1:
                        submission.mod.sticky(bottom=False)
                    else:
                        submission.mod.sticky(bottom=True)
                if post.get("contest_mode"):
                    submission.contest_mode()
                submission.disable_inbox_replies()
                # sandbox test mode
                if args.sandbox:
                    submission.reply(post["when"])
    except Exception as e:
        logging.error("exception posting {}: {}".format(post, e))
        if post and post.get("subreddit"):
            post["subreddit"].message("Scheduled post issue", "Exception generated while posting. Please investigate.")
        sys.exit(1)


def run():
    posts = []
    for subreddit in args.subreddit:
        posts.extend(read_schedule(r.subreddit(subreddit)))
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
    # main case
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
