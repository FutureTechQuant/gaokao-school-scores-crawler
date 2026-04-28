import json
import os
import time
from pathlib import Path

from .base import BaseCrawler


class SchoolScoreCrawler(BaseCrawler):
    def __init__(self):
        super().__init__()
        self._first_logged = False
        self.progress_dir = Path(os.getenv('SCHOOL_SCORE_PROGRESS_DIR', 'data/school_scores_progress'))
        self.data_dir = Path(os.getenv('SCHOOL_SCORE_DATA_DIR', 'data/school_scores'))
        self.run_deadline_seconds = int(os.getenv('SCHOOL_SCORE_RUN_DEADLINE_SECONDS', '17400'))
        self.flush_schools = max(1, int(os.getenv('SCHOOL_SCORE_FLUSH_SCHOOLS', '25')))

        self.province_dict = {
            '11': '北京', '12': '天津', '13': '河北', '14': '山西', '15': '内蒙古',
            '21': '辽宁', '22': '吉林', '23': '黑龙江',
            '31': '上海', '32': '江苏', '33': '浙江', '34': '安徽', '35': '福建', '36': '江西', '37': '山东',
            '41': '河南', '42': '湖北', '43': '湖南',
            '44': '广东', '45': '广西', '46': '海南',
            '50': '重庆', '51': '四川', '52': '贵州', '53': '云南', '54': '西藏',
            '61': '陕西', '62': '甘肃', '63': '青海', '64': '宁夏', '65': '新疆',
            '71': '台湾', '81': '香港', '82': '澳门',
        }

    def now_str(self):
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())

    def write_json_atomic(self, path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + '.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def format_duration(self, seconds):
        seconds = max(0, float(seconds))
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f'{hours}小时{minutes}分{secs}秒'
        if minutes > 0:
            return f'{minutes}分{secs}秒'
        return f'{seconds:.2f}秒'

    def get_type_name(self, type_code):
        type_map = {
            '1': '文科',
            '2': '理科',
            '3': '综合',
            '4': '物理类',
            '5': '历史类',
        }
        return type_map.get(str(type_code), f'类型{type_code}')

    def load_default_school_ids(self):
        schools_file = Path(os.getenv('SCHOOL_DATA_FILE', 'data/schools.json'))
        if not schools_file.exists():
            print(f'⚠️  未找到 schools.json: {schools_file}')
            return []

        with open(schools_file, 'r', encoding='utf-8') as f:
            payload = json.load(f)

        if isinstance(payload, list):
            schools = payload
        elif isinstance(payload, dict):
            schools = payload.get('data', [])
            if not schools and payload.get('school_id'):
                schools = [payload]
        else:
            schools = []

        school_ids = []
        for item in schools:
            if isinstance(item, dict) and item.get('school_id'):
                school_ids.append(str(item['school_id']))

        def sort_key(x):
            return (0, int(x)) if x.isdigit() else (1, x)

        school_ids = sorted(dict.fromkeys(school_ids), key=sort_key)
        sample_count = int(os.getenv('SAMPLE_SCHOOLS', '0') or 0)
        if sample_count > 0:
            school_ids = school_ids[:sample_count]
        return school_ids

    def get_progress_file(self):
        custom = os.getenv('SCHOOL_SCORE_PROGRESS_FILE', '').strip()
        if custom:
            return Path(custom)
        return self.progress_dir / 'progress.json'

    def load_progress(self, target_school_ids):
        path = self.get_progress_file()
        base = {
            'target_school_ids': [str(x) for x in target_school_ids],
            'current_school_index': 0,
            'updated_at': None,
            'last_error': None,
            'status': 'new',
        }
        if not path.exists():
            return base
        try:
            with open(path, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except Exception:
            return base

        saved_targets = [str(x) for x in progress.get('target_school_ids', [])]
        current_targets = [str(x) for x in target_school_ids]
        if saved_targets != current_targets:
            return base
        return progress

    def save_progress(self, target_school_ids, current_school_index, last_error=None, status='running'):
        payload = {
            'target_school_ids': [str(x) for x in target_school_ids],
            'current_school_index': int(current_school_index),
            'updated_at': self.now_str(),
            'last_error': last_error,
            'status': status,
        }
        self.write_json_atomic(self.get_progress_file(), payload)

    def clear_progress(self):
        path = self.get_progress_file()
        if path.exists():
            path.unlink()

    def get_file_path(self, year, province_id):
        province_name = self.province_dict.get(str(province_id), f'省份{province_id}')
        return self.data_dir / str(year) / f'{province_name}.json'

    def build_record_key(self, item):
        return (
            str(item.get('school_id') or ''),
            str(item.get('school_name') or ''),
            str(item.get('province_id') or ''),
            str(item.get('year') or ''),
            str(item.get('type') or ''),
            str(item.get('batch') or ''),
            str(item.get('min_score') or ''),
            str(item.get('min_rank') or ''),
        )

    def load_bucket(self, year, province_id):
        path = self.get_file_path(year, province_id)
        province_name = self.province_dict.get(str(province_id), f'省份{province_id}')
        records = []
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                if isinstance(payload, dict):
                    records = payload.get('data', []) or []
                elif isinstance(payload, list):
                    records = payload
            except Exception as e:
                print(f'⚠️  读取已有文件失败，改为重建: {path} - {e}')
                records = []
        existing_keys = {self.build_record_key(item) for item in records if isinstance(item, dict)}
        return {
            'year': str(year),
            'province_id': str(province_id),
            'province': province_name,
            'data': records,
            'existing_keys': existing_keys,
            'dirty': False,
        }

    def save_bucket(self, bucket):
        file_path = self.get_file_path(bucket['year'], bucket['province_id'])
        body = {
            'update_time': self.now_str(),
            'year': str(bucket['year']),
            'province_id': str(bucket['province_id']),
            'province': bucket.get('province'),
            'count': len(bucket.get('data', [])),
            'data': bucket.get('data', []),
        }
        self.write_json_atomic(file_path, body)
        bucket['dirty'] = False

    def save_dirty_buckets(self, buckets):
        for bucket in buckets.values():
            if bucket.get('dirty'):
                self.save_bucket(bucket)

    def get_school_info(self, school_id):
        url = f'https://static-data.gaokao.cn/www/2.0/school/{school_id}/info.json'
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == '0000' and 'data' in result:
                    return result['data']
        except Exception as e:
            print(f'      ⚠️  获取学校信息失败 (ID:{school_id}): {str(e)}')
        return None

    def extract_records(self, school_id, school_info):
        school_name = school_info.get('name', '未知')
        province_score_min = school_info.get('province_score_min', {}) or {}
        records = []

        if not self._first_logged and province_score_min:
            print(f'   📡 [学校最低分接口] school_id={school_id}')
            print(f'      URL: https://static-data.gaokao.cn/www/2.0/school/{school_id}/info.json')
            print('      首次响应数据结构:')
            print('      ' + '─' * 50)
            print(f'      学校名称: {school_name}')
            print(f'      province_score_min 类型: {type(province_score_min).__name__}')
            print(f'      包含省份数: {len(province_score_min)}')

            sample_province_id = list(province_score_min.keys())[0]
            sample_data = province_score_min[sample_province_id]
            print(f'      样例数据（省份ID: {sample_province_id}）:')
            print('      ' + '─' * 50)
            if isinstance(sample_data, dict):
                for key, value in sample_data.items():
                    print(f'         {key:20} = {value}')
            self._first_logged = True

        for province_id, score_data in province_score_min.items():
            if not isinstance(score_data, dict):
                continue

            year = score_data.get('year')
            if year in (None, ''):
                continue

            province_name = self.province_dict.get(str(province_id), f'省份{province_id}')
            type_code = score_data.get('type')

            records.append({
                'school_id': str(school_id),
                'school_name': school_name,
                'province_id': str(province_id),
                'province': province_name,
                'type': type_code,
                'type_name': self.get_type_name(type_code),
                'min_score': score_data.get('min'),
                'year': str(year),
                'batch': score_data.get('batch'),
                'min_rank': score_data.get('min_section'),
            })

        return records

    def merge_record(self, buckets, record):
        bucket_key = (str(record['year']), str(record['province_id']))
        if bucket_key not in buckets:
            buckets[bucket_key] = self.load_bucket(record['year'], record['province_id'])

        bucket = buckets[bucket_key]
        key = self.build_record_key(record)
        if key in bucket['existing_keys']:
            return 0

        bucket['existing_keys'].add(key)
        bucket['data'].append(record)
        bucket['dirty'] = True
        return 1

    def should_stop(self, started_at):
        return (time.time() - started_at) >= self.run_deadline_seconds

    def crawl(self, school_ids=None):
        school_ids = [str(x) for x in (school_ids or self.load_default_school_ids())]

        if not school_ids:
            print('⚠️  没有可用学校ID')
            return {
                'status': 'skipped',
                'saved_documents': 0,
                'completed_schools': 0,
            }

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.progress_dir.mkdir(parents=True, exist_ok=True)

        progress = self.load_progress(school_ids)
        start_index = int(progress.get('current_school_index', 0) or 0)
        started_at = time.time()
        buckets = {}
        added_records = 0

        print('启动大学最低分爬虫')
        print(f'学校数: {len(school_ids)}')
        print(f'软截止: {self.format_duration(self.run_deadline_seconds)}')
        print(f'学校起始索引: {start_index + 1}/{len(school_ids)}')


        for school_index in range(start_index, len(school_ids)):
            if self.should_stop(started_at):
                self.save_dirty_buckets(buckets)
                self.save_progress(
                    target_school_ids=school_ids,
                    current_school_index=school_index,
                    last_error='run deadline reached',
                    status='partial',
                )
                print('⏸️ 接近 5 小时上限，已保存数据和 progress，准备下一轮续跑')
                return {
                    'status': 'partial',
                    'saved_documents': sum(1 for bucket in buckets.values() if not bucket.get('dirty')),
                    'completed_schools': school_index,
                }

            school_id = school_ids[school_index]
            print(f'[{school_index + 1}/{len(school_ids)}] 学校ID: {school_id}', end='', flush=True)
            school_info = self.get_school_info(school_id)

            if not school_info:
                print(' ✗ 无数据')
                self.polite_sleep(0.2, 0.6)
                continue

            records = self.extract_records(school_id, school_info)
            school_name = school_info.get('name', '未知')

            if not records:
                print(f' ⚠️  {school_name} - 无分数线数据')
            else:
                school_added = 0
                for record in records:
                    school_added += self.merge_record(buckets, record)
                added_records += school_added
                print(f' ✓ {school_name} - {len(records)} 个省份，新增 {school_added} 条')

            if (school_index + 1) % self.flush_schools == 0:
                self.save_dirty_buckets(buckets)
                self.save_progress(
                    target_school_ids=school_ids,
                    current_school_index=school_index + 1,
                    last_error=None,
                    status='running',
                )
                print(f'   ↻ 已阶段性保存：学校进度 {school_index + 1}/{len(school_ids)}，本轮新增 {added_records} 条')

            self.polite_sleep(0.2, 0.6)

        self.save_dirty_buckets(buckets)
        self.clear_progress()

        saved_documents = 0
        for year_dir in self.data_dir.iterdir():
            if year_dir.is_dir():
                saved_documents += len(list(year_dir.glob('*.json')))


        print('✅ 大学最低分爬取完成！')
        print(f'   总新增: {added_records} 条')
        print(f'   输出文件数: {saved_documents}')

        return {
            'status': 'done',
            'saved_documents': saved_documents,
            'completed_schools': len(school_ids),
        }


if __name__ == '__main__':
    crawler = SchoolScoreCrawler()
    crawler.crawl()
