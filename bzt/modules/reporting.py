"""
Basics of reporting capabilities

Copyright 2015 BlazeMeter Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import os
import time
from datetime import datetime

from bzt.modules.aggregator import DataPoint, KPISet
from bzt.engine import Reporter, AggregatorListener
from bzt.modules.passfail import PassFailStatus
from bzt.six import parse, etree, StringIO
from tempfile import NamedTemporaryFile


class FinalStatus(Reporter, AggregatorListener):
    """
    A reporter that prints short statistics on test end
    """

    def __init__(self):
        super(FinalStatus, self).__init__()
        self.last_sec = None
        self.start_time = time.time()
        self.end_time = None

    def prepare(self):
        self.start_time = time.time()

    def aggregated_second(self, data):
        """
        Just store the latest info

        :type data: bzt.modules.aggregator.DataPoint
        """
        self.last_sec = data

    def post_process(self):
        """
        Log basic stats
        """
        super(FinalStatus, self).post_process()

        self.end_time = time.time()

        if self.parameters.get("test-duration", True):
            self.__report_duration()

        if self.last_sec:
            summary_kpi = self.last_sec[DataPoint.CUMULATIVE][""]

            if self.parameters.get("summary", True):
                self.__report_samples_count(summary_kpi)
            if self.parameters.get("percentiles", True):
                self.__report_percentiles(summary_kpi)

            if self.parameters.get("failed-labels", False):
                self.__report_failed_labels(self.last_sec[DataPoint.CUMULATIVE])

    def __report_samples_count(self, summary_kpi_set):
        """
        reports samples count
        """
        err_rate = 100 * summary_kpi_set[KPISet.FAILURES] / float(summary_kpi_set[KPISet.SAMPLE_COUNT])
        self.log.info("Samples count: %s, %.2f%% failures", summary_kpi_set[KPISet.SAMPLE_COUNT], err_rate)

    def __report_percentiles(self, summary_kpi_set):
        """
        reports percentiles
        """

        fmt = "Average times: total %.3f, latency %.3f, connect %.3f"
        self.log.info(fmt, summary_kpi_set[KPISet.AVG_RESP_TIME], summary_kpi_set[KPISet.AVG_LATENCY],
                      summary_kpi_set[KPISet.AVG_CONN_TIME])

        for key in sorted(summary_kpi_set[KPISet.PERCENTILES].keys(), key=float):
            self.log.info("Percentile %.1f%%: %.3f", float(key), summary_kpi_set[KPISet.PERCENTILES][key])

    def __report_failed_labels(self, cumulative):
        """
        reports failed labels
        """
        report_template = "%d failed samples: %s"
        sorted_labels = sorted(cumulative.keys())
        for sample_label in sorted_labels:
            if sample_label != "":
                failed_samples_count = cumulative[sample_label]['fail']
                if failed_samples_count:
                    self.log.info(report_template, failed_samples_count, sample_label)

    def __report_duration(self):
        """
        asks executors start_time and end_time, provides time delta
        """

        date_start = datetime.fromtimestamp(int(self.start_time))
        date_end = datetime.fromtimestamp(int(self.end_time))
        self.log.info("Test duration: %s", date_end - date_start)


class JUnitXMLReporter(Reporter, AggregatorListener):
    """
    A reporter that exports results in Jenkins JUnit XML format.
    """

    REPORT_FILE_NAME = "xunit"
    REPORT_FILE_EXT = ".xml"

    def __init__(self):
        super(JUnitXMLReporter, self).__init__()
        self.report_file_path = None
        self.last_second = None

    def prepare(self):
        """
        create artifacts, parse options.
        report filename from parameters
        :return:
        """
        filename = self.parameters.get("filename", None)
        if filename:
            self.report_file_path = filename
        else:
            self.report_file_path = self.engine.create_artifact(JUnitXMLReporter.REPORT_FILE_NAME,
                                                                JUnitXMLReporter.REPORT_FILE_EXT)
        self.parameters["filename"] = self.report_file_path

    def aggregated_second(self, data):
        """
        :param data:
        :return:
        """
        self.last_second = data

    def post_process(self):
        """
        Get report data, generate xml report.
        """
        super(JUnitXMLReporter, self).post_process()
        test_data_source = self.parameters.get("data-source", "sample-labels")

        if self.last_second:
            # data-source sample-labels
            if test_data_source == "sample-labels":
                tmp_file_name = self.process_sample_labels()
                # root_xml_element = self.__process_sample_labels()
                self.save_report(tmp_file_name, self.report_file_path)
            # data-source pass-fail
            elif test_data_source == "pass-fail":
                root_xml_element = self.__process_pass_fail()
                self.__save_report(root_xml_element)

    def process_sample_labels(self):

        with NamedTemporaryFile(suffix=".xml", delete=False, dir=self.engine.artifacts_dir) as tmp_xml_file:

            _kpiset = self.last_second[DataPoint.CUMULATIVE]
            summary_kpiset = _kpiset[""]
            xml_writer = JUnitXMLWriter(tmp_xml_file)
            self.write_summary_report(xml_writer, summary_kpiset)

            # testcase_template = '  <testcase classname="{classname}" name="{name}" time="0">\n'
            # err_template = '    <error message="{message}" type="{type}">'

            for key in sorted(_kpiset.keys()):
                if key != "":  # if label is not blank
                    class_name, resource_name = JUnitXMLReporter.__convert_label_name(key)
                    # test_case = testcase_template.format(classname=class_name, name=resource_name)
                    xml_writer.add_testcase(close=False, classname=class_name, name=resource_name)
                    # tmp_xml_file.write(test_case)

                    if _kpiset[key][KPISet.ERRORS]:
                        for er_dict in _kpiset[key][KPISet.ERRORS]:
                            err_message = str(er_dict["rc"])
                            err_type = str(er_dict["msg"])
                            err_desc = "total errors of this type:" + str(er_dict["cnt"])
                            # err = err_template.format(message=err_message, type=err_type)
                            xml_writer.add_error(close=False, message=err_message, type=err_type)
                            # tmp_xml_file.write(err)
                            xml_writer.raw_write(err_desc)
                            xml_writer.close_element()

                            # tmp_xml_file.write("</error>\n")
                    # tmp_xml_file.write("  </testcase>\n")
                    xml_writer.close_element()
            # tmp_xml_file.write("</testsuite>")
            xml_writer.close_element()
        return tmp_xml_file.name

    def write_summary_report(self, xml_writer, summary_kpiset):
        """
        writes testcase class_name="summary"
        :return: tmp xml filename
        """

        succ = str(summary_kpiset[KPISet.SUCCESSES])
        throughput = str(summary_kpiset[KPISet.SAMPLE_COUNT])
        fail = str(summary_kpiset[KPISet.FAILURES])

        # testsuite_template = "<testsuite failures='{failures}' name='{test_name}' skip='0' tests='{tests}'>\n"
        # tmp_xml_file.write(testsuite_template.format(failures=fail, test_name="sample_labels", tests=throughput))

        xml_writer.add_testsuite(close=False, failures=fail, name='sample_labels', skip='0', tests=throughput)

        # summary_test_case_template = '  <testcase classname="bzt" name="summary_report">\n'
        # tmp_xml_file.write(summary_test_case_template)

        xml_writer.add_testcase(close=False, classname="bzt", name="summary_report")

        # errors_report_template = '    <error message="error statistics:" type="http error">\n'
        # tmp_xml_file.write(errors_report_template)

        xml_writer.add_error(close=False, message="error statistics:", type="http error")

        errors_count = str(self.count_errors(summary_kpiset))

        summary_report_template = "Success: {success}, Sample count: {throughput}, " \
                                  "Failures: {fail}, Errors: {errors}\n"
        summary_report = summary_report_template.format(success=succ, throughput=throughput, fail=fail,
                                                        errors=errors_count)
        xml_writer.raw_write(summary_report)

        self.write_errors(xml_writer, summary_kpiset)
        xml_writer.close_element()
        xml_writer.close_element()

    def count_errors(self, summary_kpi_set):
        """
        Returns overall errors count
        :return:
        """
        err_counter = 0  # used in summary report (summary report)

        for error in summary_kpi_set[KPISet.ERRORS]:
            # enumerate urls and count errors (from Counter object)
            for _url, _err_count in error["urls"].items():
                err_counter += _err_count

        return err_counter

    def write_errors(self, xml_writer, summary_kpi_set):
        """
        Writes error descriptions in summary_report
        :return:
        """
        err_template = "Error code: {rc}, Message: {msg}, count: {cnt}\n"
        url_err_template = "URL: {url}, Error count {cnt}\n"
        urls_err_string = ""
        for error in summary_kpi_set[KPISet.ERRORS]:
            xml_writer.raw_write(err_template.format(rc=error['rc'], msg=error['msg'], cnt=error['cnt']))
            for _url, _err_count in error["urls"].items():
                urls_err_string += url_err_template.format(url=_url, cnt=str(_err_count))
                xml_writer.raw_write(urls_err_string)

    @staticmethod
    def __convert_label_name(url):
        """
        http://some.address/path/resource?query -> http.some_address.path.resource.query
        :param url:
        :return: string
        """

        # split url on domain resource, protocol, etc
        parsed_url = parse.urlparse(url)
        # remove dots from url and join all pieces on dot
        # small fix needed - better do not use blank pieces
        if parsed_url.scheme:
            class_name = parsed_url.scheme + "." + parsed_url.netloc.replace(".", "_")
            resource_name = ".".join([parsed_url.path.replace(".", "_"),
                                      parsed_url.params.replace(".", "_"),
                                      parsed_url.query.replace(".", "_"),
                                      parsed_url.fragment.replace(".", "_")])
        else:
            class_name = url
            resource_name = url
        return class_name, resource_name

    # def __save_report(self, root_node):
    #     """
    #     :param root_node:
    #     :return:
    #     """
    #     try:
    #         if os.path.exists(self.report_file_path):
    #             self.log.warning("File %s already exists, will be overwritten", self.report_file_path)
    #         else:
    #             dirname = os.path.dirname(self.report_file_path)
    #             if dirname and not os.path.exists(dirname):
    #                 os.makedirs(dirname)
    #
    #         etree_obj = etree.ElementTree(root_node)
    #         self.log.info("Writing JUnit XML report into: %s", self.report_file_path)
    #         with open(self.report_file_path, 'wb') as _fds:
    #             etree_obj.write(_fds, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    #
    #     except BaseException:
    #         self.log.error("Cannot create file %s", self.report_file_path)
    #         raise

    def save_report(self, tmp_name, orig_name):
        """

        :param tmp_name:
        :param orig_name:
        :return:
        """
        os.rename(tmp_name, orig_name)

    def __process_pass_fail(self):
        """

        :return: etree xml root element
        """
        pass_fail_objects = [_x for _x in self.engine.reporters if isinstance(_x, PassFailStatus)]
        fail_criterias = []
        for pf_obj in pass_fail_objects:
            if pf_obj.criterias:
                for _fc in pf_obj.criterias:
                    fail_criterias.append(_fc)
        # count total failed tests, tests, create root <testsuite>
        failures = [x for x in fail_criterias if x.is_triggered and x.fail]
        total_failed = str(len(failures))
        tests_count = str(len(fail_criterias))
        root_xml_element = etree.Element("testsuite", name="taurus_junitxml_pass_fail", tests=tests_count,
                                         failures=total_failed, skip="0")
        for fc_obj in fail_criterias:
            if fc_obj.config['label']:
                data = (fc_obj.config['subject'], fc_obj.config['label'],
                        fc_obj.config['condition'], fc_obj.config['threshold'])
                tpl = "%s of %s%s%s"
            else:
                data = (fc_obj.config['subject'], fc_obj.config['condition'], fc_obj.config['threshold'])
                tpl = "%s%s%s"

            if fc_obj.config['timeframe']:
                tpl += " for %s"
                data += (fc_obj.config['timeframe'],)
            classname = tpl % data

            fc_xml_element = etree.SubElement(root_xml_element, "testcase", classname=classname, name="")
            if fc_obj.is_triggered and fc_obj.fail:
                # NOTE: we can add error description im err_element.text()
                etree.SubElement(fc_xml_element, "error", type="pass/fail criteria triggered", message="")

        # FIXME: minor fix criteria representation in report
        return root_xml_element


class JUnitXMLWriter(object):
    """
    Writes report in JUnitXML format in file
    """

    def __init__(self, fds):
        self.fds = fds
        self.endings = []
        self.write_header()

    def add_element(self, element_name, text="", close=True, **kwargs):
        """
        adds element
        :param element_name:
        :param kwargs:
        :return:
        """
        self.fds.write("<%s" % element_name)
        for k, v in kwargs.items():
            self.fds.write(" %s='%s'" % (k, v))
        self.fds.write(">\n")
        if text:
            self.fds.write(text)
            self.fds.write("\n")
        if close:
            self.fds.write("</%s>\n" % element_name)
        else:
            self.endings.append("</%s>\n" % element_name)

    def add_testsuite(self, close=True, **kwargs):
        self.add_element("testsuite", close=close, **kwargs)

    def add_testcase(self, close=True, **kwargs):
        self.add_element("testcase", close=close, **kwargs)

    def add_error(self, close=True, **kwargs):
        self.add_element("error", close=close, **kwargs)

    def close_element(self):
        if self.endings:
            self.fds.write(self.endings.pop())

    def write_header(self):
        self.fds.write("<?xml version='1.0' encoding='UTF-8'?>\n")

    def raw_write(self, data):
        self.fds.write(data)