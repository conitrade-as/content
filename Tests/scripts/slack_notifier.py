import json
import os
import re
import argparse
import requests
from circleci.api import Api as circle_api

from slackclient import SlackClient

from demisto_sdk.commands.common.tools import str2bool, run_command, LOG_COLORS, print_color, print_error

DEMISTO_GREY_ICON = 'https://3xqz5p387rui1hjtdv1up7lw-wpengine.netdna-ssl.com/wp-content/' \
                    'uploads/2018/07/Demisto-Icon-Dark.png'
UNITTESTS_TYPE = 'unittests'
TEST_PLAYBOOK_TYPE = 'test_playbooks'
SDK_UNITTESTS_TYPE = 'sdk_unittests'
SDK_FAILED_STEPS_TYPE = 'sdk_faild_steps'
SDK_RUN_AGAINST_FAILED_STEPS_TYPE = 'sdk_run_against_failed_steps'


def get_faild_steps_list():
    options = options_handler()
    failed_steps_list = []
    circle_client = circle_api(options.circleci)
    vcs_type = 'github'
    build_report = circle_client.get_build_info(username='demisto', project='content', build_num=options.buildNumber,
                                                vcs_type=vcs_type)
    for step in build_report.get('steps', []):
        step_name = step.get('name', '')
        actions = step.get('actions', [])
        for action in actions:
            action_status = action.get('status', '')
            if action_status and action_status == 'failed':
                action_name = action.get('name', '')
                if action_name != step_name:
                    failed_steps_list.append(f'{step_name}: {action_name}')
                else:
                    failed_steps_list.append(f'{step_name}')

    return failed_steps_list


def http_request(url, params_dict=None, verify=True, text=False):
    res = requests.request("GET",
                           url,
                           verify=verify,
                           params=params_dict,
                           )
    res.raise_for_status()

    if text:
        return res.text
    return res.json()


def options_handler():
    parser = argparse.ArgumentParser(description='Parser for slack_notifier args')
    parser.add_argument('-n', '--nightly', type=str2bool, help='is nightly build?', required=True)
    parser.add_argument('-u', '--url', help='The url of the current build', required=True)
    parser.add_argument('-b', '--buildNumber', help='The build number', required=True)
    parser.add_argument('-s', '--slack', help='The token for slack', required=True)
    parser.add_argument('-c', '--circleci', help='The token for circleci', required=True)
    parser.add_argument('-t', '--test_type', help='unittests or test_playbooks or sdk_unittests or sdk_faild_steps')
    parser.add_argument('-f', '--env_results_file_name', help='The env results file containing the dns address',
                        required=True)
    options = parser.parse_args()

    return options


def get_failing_unit_tests_file_data():
    try:
        failing_ut_list = None
        file_name = './artifacts/failed_lint_report.txt'
        if os.path.isfile(file_name):
            print('Extracting lint_report')
            with open(file_name, 'r') as failed_unittests_file:
                failing_ut = failed_unittests_file.readlines()
                failing_ut_list = [line.strip('\n') for line in failing_ut]
        else:
            print('Did not find failed_lint_report.txt file')
    except Exception as err:
        print_error(f'Error getting failed_lint_report.txt file: \n {err}')
    return failing_ut_list


def get_entities_fields(entity_title, report_file_name=''):
    if 'lint' in report_file_name:  # lint case
        failed_entities = get_failing_unit_tests_file_data()
    else:
        failed_entities = get_faild_steps_list()
    entity_fields = []
    if failed_entities:
        entity_fields.append({
            "title": f'{entity_title} - ({len(failed_entities)})',
            "value": '\n'.join(failed_entities),
            "short": False
        })
    return entity_fields


def get_attachments_for_unit_test(build_url, is_sdk_build=False):
    unittests_fields = get_entities_fields(entity_title="Failed Unittests", report_file_name="failed_lint_report")
    color = 'good' if not unittests_fields else 'danger'
    if not unittests_fields:
        title = 'Content Nightly Unit Tests - Success' if not is_sdk_build else 'SDK Nightly Unit Tests - Success'
    else:
        title = 'Content Nightly Unit Tests - Failure' if not is_sdk_build else 'SDK Nightly Unit Tests - Failure'
    content_team_attachment = [{
        'fallback': title,
        'color': color,
        'title': title,
        'title_link': build_url,
        'fields': unittests_fields
    }]
    return content_team_attachment


def get_attachments_for_all_steps(build_url, title='SDK Nightly Build'):
    steps_fields = get_entities_fields(entity_title="Failed Steps")
    color = 'good' if not steps_fields else 'danger'
    title = f'{title} - Success' if not steps_fields else '{title} - Failure'

    container_build_url = build_url + '#queue-placeholder/containers/0'
    content_team_attachment = [{
        'fallback': title,
        'color': color,
        'title': title,
        'title_link': container_build_url,
        'fields': steps_fields
    }]
    return content_team_attachment


def get_attachments_for_test_playbooks(build_url, env_results_file_name):
    with open(env_results_file_name, 'r') as env_results_file_content:
        env_results = json.load(env_results_file_content)

    role = env_results[0]['Role']
    success_file_path = "./Tests/is_build_passed_{}.txt".format(role.replace(' ', ''))

    content_team_fields, content_fields, _ = get_fields()
    is_build_success = os.path.isfile(success_file_path)
    color = 'good' if is_build_success else 'danger'
    title = 'Content Nightly Build - Success' if is_build_success else 'Content Nightly Build - Failure'

    content_team_attachment = [{
        'fallback': title,
        'color': color,
        'title': title,
        'title_link': build_url,
        'fields': content_team_fields
    }]

    content_attachment = [{
        'fallback': title,
        'color': color,
        'title': title,
        'title_link': build_url,
        'fields': content_fields
    }]

    return content_team_attachment, content_attachment


def get_fields():
    failed_tests = []
    if os.path.isfile('./Tests/failed_tests.txt'):
        print('Extracting failed_tests')
        with open('./Tests/failed_tests.txt', 'r') as failed_tests_file:
            failed_tests = failed_tests_file.readlines()
            failed_tests = [line.strip('\n') for line in failed_tests]

    skipped_tests = []
    if os.path.isfile('./Tests/skipped_tests.txt'):
        print('Extracting skipped_tests')
        with open('./Tests/skipped_tests.txt', 'r') as skipped_tests_file:
            skipped_tests = skipped_tests_file.readlines()
            skipped_tests = [line.strip('\n') for line in skipped_tests]

    skipped_integrations = []
    if os.path.isfile('./Tests/skipped_integrations.txt'):
        print('Extracting skipped_integrations')
        with open('./Tests/skipped_integrations.txt', 'r') as skipped_integrations_file:
            skipped_integrations = skipped_integrations_file.readlines()
            skipped_integrations = [line.strip('\n') for line in skipped_integrations]

    content_team_fields = []
    content_fields = []
    if failed_tests:
        field_failed_tests = {
            "title": "Failed tests - ({})".format(len(failed_tests)),
            "value": '\n'.join(failed_tests),
            "short": False
        }
        content_team_fields.append(field_failed_tests)
        content_fields.append(field_failed_tests)

    if skipped_tests:
        field_skipped_tests = {
            "title": "Skipped tests - ({})".format(len(skipped_tests)),
            "value": '',
            "short": True
        }
        content_team_fields.append(field_skipped_tests)

    if skipped_integrations:
        field_skipped_integrations = {
            "title": "Skipped integrations - ({})".format(len(skipped_integrations)),
            "value": '',
            "short": True
        }
        content_team_fields.append(field_skipped_integrations)

    return content_team_fields, content_fields, failed_tests


def slack_notifier(build_url, slack_token, test_type, env_results_file_name=None):
    branches = run_command("git branch")
    branch_name_reg = re.search(r'\* (.*)', branches)
    branch_name = branch_name_reg.group(1)

    if branch_name == 'master':
        print("Extracting build status")
        if test_type == UNITTESTS_TYPE:
            print_color("Starting Slack notifications about nightly build - unit tests", LOG_COLORS.GREEN)
            content_team_attachments = get_attachments_for_unit_test(build_url)
        elif test_type == SDK_UNITTESTS_TYPE:
            print_color("Starting Slack notifications about SDK nightly build - unit tests", LOG_COLORS.GREEN)
            content_team_attachments = get_attachments_for_unit_test(build_url, is_sdk_build=True)
        elif test_type == 'test_playbooks':
            print_color("Starting Slack notifications about nightly build - tests playbook", LOG_COLORS.GREEN)
            content_team_attachments, _ = get_attachments_for_test_playbooks(build_url, env_results_file_name)
        elif test_type == SDK_FAILED_STEPS_TYPE:
            print_color('Starting Slack notifications about SDK nightly build - test playbook', LOG_COLORS.GREEN)
            content_team_attachments = get_attachments_for_all_steps(build_url)
        elif test_type == SDK_RUN_AGAINST_FAILED_STEPS_TYPE:
            content_team_attachments = get_attachments_for_all_steps(
                build_url, title='Demisto SDK Nightly - Run Against Cortex XSOAR'
            )
        else:
            raise NotImplementedError('The test_type parameter must be only \'test_playbooks\' or \'unittests\'')
        print('Content team attachments:\n', content_team_attachments)
        print("Sending Slack messages to #content-team")
        slack_client = SlackClient(slack_token)
        slack_client.api_call(
            "chat.postMessage",
            channel="dmst-content-team",
            username="Content CircleCI",
            as_user="False",
            attachments=content_team_attachments
        )


def main():
    options = options_handler()
    if options.nightly:
        slack_notifier(options.url,
                       options.slack,
                       options.test_type,
                       env_results_file_name=options.env_results_file_name)
    elif options.test_type in (SDK_UNITTESTS_TYPE, SDK_FAILED_STEPS_TYPE, SDK_RUN_AGAINST_FAILED_STEPS_TYPE):
        slack_notifier(options.url, options.slack, options.test_type)
    else:
        print_color("Not nightly build, stopping Slack Notifications about Content build", LOG_COLORS.RED)


if __name__ == '__main__':
    main()
