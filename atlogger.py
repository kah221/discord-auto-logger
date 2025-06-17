# 250515_0058~250618_0229
# discord-ATLogger VC入場退場を記録するBot

# 機能
# ■【1.】 /check-csv    csvファイルの状況を確認する
# ■【2.】 /this         現在の経過時間を教えてくれる（作業中のみ実行可能）
# ■【3.】 /today        今日の合計作業時間を教えてくれる
# ■【4.】 /yesterday    昨日の合計作業時間を教えてくれる
# ■【5.】 /span         期間を指定して合計作業時間を計算する（日付フォーマットは2025/7/22なら250722）
# ■【6.】 自動週間報告   毎週月曜05:00に先週1週間の合計作業時間を報告する

# Projectメモ
# https://docs.google.com/document/d/1YIry-NB4oGFjcb8cDbHiu1Jh-xhcxOCAYoPIzS-NZjE/edit?tab=t.0

# 更新ログ
# 250517_2317~ テスト運用
# 250609_0230~ 本稼働
# 250618_0229  githubアップロードのため体裁整え


import discord
from discord import app_commands # スラッシュコマンドの実装に必要
import os
from dotenv import load_dotenv
import datetime
import csv
from apscheduler.schedulers.asyncio import AsyncIOScheduler # スケジューラ 要 pip install apscheduler
from apscheduler.triggers.cron import CronTrigger # スケジューラ
import re
from collections import deque # csvデータをキューとして扱うため


# ------------------------------
# ↓ 変数定義
# ------------------------------


load_dotenv()

# discordボットの設定
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.voice_states = True # ボイスチャンネルの状態を取得するために必要（intentに追加という処理）
client = discord.Client(intents=intents)
client.wait_until_ready()
tree = app_commands.CommandTree(client)

# スケジューラの設定
scheduler = AsyncIOScheduler() # インスタンス作成
summary_output_TCID = os.getenv("SUMMARY_OUTPUT_TCID") # 【自動】週間報告のテキストチャンネルID
# summary_output_TCID = os.getenv("TEST_LOG_ID") # testsa-ba-のログ出力専用チャンネルのID（デバッグ用）

# デバッグ用出力先（DM）
# DMID = os.getenv("LOG_OUTPUT_ID_DM")

# 日付表示のためのフォーマット
date_format = "%m/%d" # 月/日
my_date_format = "%y%m%d" # 年月日 期間指定用の自分がよく使うフォーマット

# 作業鯖の記録対象のVCIDをリストにしておく（VCを追加するたびに手動で追記すること, この中に含まれないVCに参加しているときは作業時間に加算しない）
target_vcids = [
    # os.getenv("TEST_VC_ID"), # testsa-ba-
    os.getenv("TARGET_VC_ID_1"), # 作業鯖 No.1 ← 個人作業用
    os.getenv("TARGET_VC_ID_2"), # 作業鯖 No.2 ← 個人作業用
    os.getenv("TARGET_VC_ID_3"), # 作業鯖 No.3 ← 個人作業用
    os.getenv("TARGET_VC_ID_A") # 作業鯖 A ← 複数人作業用
]
print(f">>> target VC: {target_vcids}")

# ユーザのVC状態変数
active_vc_user_state = {} # ←この中にユーザ毎に辞書型で格納していく
'''
↑active_vc_user_stateの説明
ユーザIDをキーとして各種情報を配列にして格納する
例)
{
    'user_id': {ユーザ名, 計測開始時刻, 前回一時退出時の累計経過時間},
    '1234567890123456789': {
        'user_name': 'kah221',
        'start_time': datetimeｵﾌﾞｼﾞｪｸﾄ,
        'total_time': timedeltaｵﾌﾞｼﾞｪｸﾄ
        },
    ...
}
'''

# csv関連
csv_path = 'log.csv'
header = ['user_id', 'user_name', 'start_time', 'end_time', 'total_time']


# ------------------------------
# ↑ 変数定義
# ↓ その他の関数
# ------------------------------


# 記録対象VC入場時の処理
def vc_join(member):
    new_user(member) # active_vc_user_stateに新しくユーザを追加する関数を呼び出す
    return


# VC退場時の処理
def vc_exit(member):
    global active_vc_user_state
    # 記録対象外のVCから退場した場合はactive_vc_user_stateにユーザが登録されていないので何もしないようにする
    if member.id in active_vc_user_state:
        # 作業時間計算
        total_time = datetime.datetime.now() - active_vc_user_state[member.id]['start_time']
        # csv書込
        write_csv(
            user_id = member.id,
            user_name = active_vc_user_state[member.id]['user_name'],
            start_time = active_vc_user_state[member.id]['start_time'],
            end_time = datetime.datetime.now(),
            total_time = total_time # 計算した値
        )
        # active_vc_user_stateから対象ユーザを削除
        del active_vc_user_state[member.id]


# active_vc_user_stateに新しくユーザを追加する関数
def new_user(member):
    global active_vc_user_state
    # active_vc_user_stateに新規ユーザとして辞書の1要素として追加 → 'user_id': {現在時刻, 0} を追加
    active_vc_user_state[member.id] = {
        'user_name': member.display_name,
        'start_time': datetime.datetime.now(),
        'total_time': datetime.timedelta(0)
    }
    print(f">>> active_vc_user_state updated: {active_vc_user_state}")


# csvファイルに書き込む関数
def write_csv(user_id, user_name, start_time, end_time, total_time):
    global csv_path

    # ユーザ名のバリデーション処理
    '''
    csvファイルを1行ずつループ読み込む際に, split()を使いたいが, 「,」「"」「改行コード」等が含まれていてはダメなので
    csvに書き込む前にこれらを削除する
    （discordの表示名には「,」「"」が使えてしまう）
    '''
    user_name = re.sub(r'[,\\"\n\r]', '', user_name)

    with open(csv_path, 'a', newline='', encoding='utf-8') as f: # appendモードで追記
        writer = csv.writer(f) # writerオブジェクトを作成
        writer.writerow([user_id, user_name, start_time, end_time, total_time])
    print(f">>> csv updated")


# [timedelta]を見やすく整形する関数→[string]
def formatTimeDelta(delta):
    # 0秒だった時
    if delta == datetime.timedelta(0):
        return "0秒"
    else:
        total_seconds = int(delta.total_seconds()) # timedeltaｵﾌﾞｼﾞｪｸﾄを全て秒単位に換算
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        r = []
        if h > 0:
            r.append(f"{h}時間")
        if m > 0:
            r.append(f"{m}分")
        if s > 0:
            r.append(f"{s}秒")

        return " ".join(r)



# 指定した日付の合計作業時間を計算する関数
# 【3.】【4.】【5.】で参照
def sum_oneday(user_id, target_date):
    global csv_path

    daily_total_time = datetime.timedelta(0) # 合計作業時間を格納する変数

    # 集計対象日の開始時刻と終了時刻を定義
    day_start = datetime.datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    day_end = day_start + datetime.timedelta(days=1) # 翌日の0時

    try:
        with open(csv_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f) # readerオブジェクトを作成

            header = next(reader) # ヘッダーを読み飛ばす

            try:
                # indexを取得
                user_id_index = header.index('user_id') # ユーザID
                start_time_index = header.index('start_time') # 開始時刻
                end_time_index = header.index('end_time') # 終了時刻
            except ValueError as e:
                print(f">>> Error: {e}")
                return datetime.timedelta(0) # ヘッダーが見つからなかった場合は0を返す

            for row in reader:
                # 行の長さがヘッダーと一致しない場合はスキップ（不正な行）
                if len(row) != len(header):
                    print(f">>> a row of csv was skipped: {row}")
                    continue

                # 取得したｲﾝﾃﾞｯｸｽを使って、各列の値を取得
                user_id_csv = row[user_id_index]
                start_time_csv = row[start_time_index]
                end_time_csv = row[end_time_index]

                # 指定ユーザーのレコードか確認
                # CSVのIDが文字列なので、target_user_idがintの場合はstr()で変換して比較
                if str(user_id_csv) != str(user_id):
                    continue

                # 日時文字列を datetime オブジェクトに変換
                try:
                    # CSVの時刻文字列はマイクロ秒まで含んでいるため、'%Y-%m-%d %H:%M:%S.%f' を使用
                    session_start_dt = datetime.datetime.strptime(start_time_csv, '%Y-%m-%d %H:%M:%S.%f')
                    session_end_dt = datetime.datetime.strptime(end_time_csv, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError as e:
                    print(f">>> a row of csv was skipped ({e}): {row}")
                    continue # 日時形式が不正ならその行はスキップ

                # セッションが対象日と全く重なっていないか判定
                # セッション終了時刻が対象日の開始時刻より前、またはセッション開始時刻が翌日の開始時刻（対象日の終了時刻）より後の場合 ★
                if session_end_dt <= day_start or session_start_dt >= day_end:
                    continue # 対象日と重なっていないのでスキップ

                # セッションが対象日と重なっている場合、対象日内の期間を計算

                # セッションの開始時刻と対象日の開始時刻の遅い方
                effective_start_dt = max(session_start_dt, day_start)

                # セッションの終了時刻と翌日の開始時刻（対象日の終了時刻）の早い方
                effective_end_dt = min(session_end_dt, day_end)

                # 対象日内のセッション継続時間
                segment_duration = effective_end_dt - effective_start_dt

                # 計算された期間が正の値であることを確認（稀に開始>終了になるデータがある場合に備え）
                if segment_duration > datetime.timedelta(0):
                    daily_total_time += segment_duration

    except FileNotFoundError:
        print(f">>> csv file was not found: {csv_path}")
        return datetime.timedelta(0)
    except Exception as e:
        print(f">>> unexpected error about csv: {e}")
        return datetime.timedelta(0)

    return daily_total_time


# 週間報告のユーザ毎に先週1週間分の合計を計算する関数
# スラッシュコマンドによるユーザ単体の合計を計算するのでは無いため、引数はuser_idを受け取らない
# 辞書で返す
# 【6.】で参照
def sum_span():
    summary_dict = {} # ユーザ毎の合計作業時間を格納する辞書（戻り値）
    '''
    summary_dictの中身
    {
        user_id[str]: {
            user_name: user_name[str]
            session_count: user_session_count[str], # セッション数（ログの数）
            total_time: user_total_time[timedelta], # 合計作業時間（そのユーザの）
            },
        user_id.....
    }
    '''

    now = datetime.datetime.now()
    span_end = datetime.datetime(now.year, now.month, now.day, 0, 0, 0) # 本日の日付
    span_start = span_end - datetime.timedelta(weeks=1) # 今日の日付から1週間分をマイナス

    print(f'>>> {span_start} <= 集計期間 < {span_end} ')

    # csv
    global csv_path, header

    # 時間で絞る
    csv_mini = [] # 先週1週間分の行を格納するリスト
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f) # readerオブジェクトを作成
        header = next(reader) # ヘッダーを読み飛ばす
        try:
            # indexを取得
            user_id_index = header.index('user_id') # ユーザID
            user_name_index = header.index('user_name') # ユーザ名
            start_time_index = header.index('start_time') # 開始時刻
            end_time_index = header.index('end_time') # 終了時刻
        except ValueError as e:
            print(f">>> error about csv header: {e}")
            return datetime.timedelta(0) # ヘッダーが見つからなかった場合は0を返す

        # csvの全行から先週1週間分の行だけ抜き取る → csv_mini
        for row in reader:
            # 行の長さがヘッダーと一致しない場合はスキップ（不正な行）
            if len(row) != len(header):
                print(f">>> a row of csv was skipped: {row}")
                continue

            csv_to_datetime_format = "%Y-%m-%d %H:%M:%S.%f" # CSVの時刻文字列はマイクロ秒まで含んでいるため、'%Y-%m-%d %H:%M:%S.%f' を使用
            # 取得したｲﾝﾃﾞｯｸｽを使って、各列の値を取得
            user_id_csv = row[user_id_index]
            user_name_csv = row[user_name_index]
            start_time_csv = datetime.datetime.strptime(row[start_time_index], csv_to_datetime_format) # datetimeオブジェクトに変換しておく
            end_time_csv = datetime.datetime.strptime(row[end_time_index], csv_to_datetime_format) # datetimeオブジェクトに変換しておく

            # 型を合わせた状態でrowとする
            row = [user_id_csv, user_name_csv, start_time_csv, end_time_csv]

            # 比較のため start_time_csvだけは、span_startに合わせて 時分秒を0にする
            start_time_csv_zero = datetime.datetime(start_time_csv.year, start_time_csv.month, start_time_csv.day, 0, 0, 0)

            # 開始時刻が指定期間内にあるかどうかを判断
            if start_time_csv_zero >= span_start and end_time_csv < span_end:
                # 条件を満たせばcsv_miniに追加
                csv_mini.append(row)

    # csv閉じる
    # 以降, csv_mini[2次元リスト]に対して操作する csv_miniにはheaderとtotal_time列が無いことに注意
    # print(f'>>> csv_mini: {csv_mini}')

    # 活動したユーザのIDを取得
    user_ids = set() # 活動したユーザのIDを格納する集合
    for row in csv_mini:

        user_id_csv = row[user_id_index] # 取得したｲﾝﾃﾞｯｸｽを使って、各列の値を取得

        if user_id_csv not in user_ids:
            user_ids.add(user_id_csv) # ユーザIDをセットに追加
    print(f">>> users with activity in the past week: {user_ids}")

    # 活動したユーザのID毎に合計作業時間を計算
    for user_id in user_ids:
        user_session_count = 0 # セッション数をカウントする変数
        user_total_time = datetime.timedelta(0) # 合計作業時間を格納する変数
        '''
        csv_mini
        [i][0]: user_id
        [i][1]: user_name ←ログの中でも最新のユーザ名（ニックネーム）を取得するため（interactionではなく自動トリガーであり, ユーザ名を簡単に取得できない様な気がしたから）
        [i][2]: start_time
        [i][3]: end_time
        '''

        # csv_miniをループでユーザIDが一致する行について処理
        print(f'>>> looking at {user_id} ...')
        for row in csv_mini:
            if row[0] == user_id: # ユーザID一致
                user_session_count += 1
                user_total_time += row[3] - row[2] #end_time - start_time = [timedelta]
                user_name = row[1]
        # 1ユーザのデータが得られたので, summary_dictにキーをユーザID, 値を{セッション数, 合計時間}の辞書として格納する
        summary_dict[user_id] = {
            'user_name': user_name, # str
            'session_count': user_session_count, # str
            'total_time': user_total_time # timedelta
        }
        print(f'>>> summary_dict: {summary_dict}')

    return summary_dict


# 直近のデータをcsvファイルから取得し2次元配列で返す関数
# /check-csv
def check_csv_metadata(how_many):
    global csv_path
    # row_count = -1 # headerがあるので
    row_count = 0 # 修正後
    # last_n_rows_list = []
    last_n_rows_str = ''
    file_size_bytes = 0
    # all_rows = [] # 全行を一時的に格納するリスト
    try:
        # ファイルサイズ
        file_size_bytes = os.path.getsize(csv_path)

        # 最後のn行
        with open(csv_path, 'r', newline='', encoding='utf-8') as f: # readerを使わずにfに対して直接forを回す
            # for row in f:
            #     # row = row.strip() # 改行コードを消す
            #     row_count += 1
            #     # last_n_rowsの行数を見て追加削除判断
            #     if len(last_n_rows_list) > how_many: # 指定行を超えていれば
            #         last_n_rows_list.pop() # 先頭の行を消す
            #     last_n_rows_list.append(row) # stlip()で配列にせずにカンマを含む文字列で格納

            # ヘッダー行を読み飛ばす
            header = next(f, None)

            # 最大長(maxlen)を指定してdequeを作成
            last_n_rows = deque(f, maxlen=how_many) # 自動的にリストの長さがhow_manyに保たれる

            # dequeはファイル全体を読み込むため、行数は別途計算
            # (もしファイルが巨大でなければ、ここで再度ファイルを開いて行数を数える)
            f.seek(0) # ファイルポインタを先頭に戻す
            next(f) # ヘッダーを飛ばす
            row_count = sum(1 for line in f)

        # 文字列にする
        # last_n_rows_str = last_n_rows_list.join("\n") # 改行コードで結合
        # last_n_rows_str = "".join(last_n_rows_list) # 改行コード無しで結合
        last_n_rows_str = "".join(last_n_rows) # dequeオブジェクトを文字列に結合

    except Exception as e:
        print(f'>>> csv file error: {e}')

    # print(f'last_n_rows_list: \n{last_n_rows_list}')
    # print(f'last_n_rows_str: \n{last_n_rows_str}')

    return {
        'size_byte': str(file_size_bytes),
        'row_count': str(row_count),
        'last_n_rows': last_n_rows_str # str
    }


# ------------------------------
# ↑ その他の関数
# ↓ イベントハンドラ
# ------------------------------


# VC入退場を検知する関数
@client.event
async def on_voice_state_update(member, before, after):
    global active_vc_user_state
    """
    ユーザーのボイスチャンネルの状態が更新されたときに実行されるイベントハンドラ
    Args:
        member (discord.Member): ボイス状態が変化したメンバー
        before (discord.VoiceState): 状態が変化する前のボイス状態
        after (discord.VoiceState): 状態が変化した後のボイス状態
    """
    # VC入場時
    if before.channel is None and after.channel is not None:
        # VC入場時の処理
        print(f">>> {member.display_name} が {after.channel.name} に入場しました")

        # 1. 記録対象のVCかどうかを確認
        if str(after.channel.id) in target_vcids: # 左辺は文字列str()にしないとダメ
            print(f">>> 対象VCは記録対象です: {after.channel.name} ({after.channel.id})")

            # 2. active_vc_user_stateに対象ユーザがいるか確認 無ければ記録開始
            if member.id not in active_vc_user_state:
                print(f'>>> 記録開始')
                vc_join(member)
        else:
            print(f">>> 対象VCは記録対象ではありません: {after.channel.name} ({after.channel.id})")

    # VC退場時
    if after.channel is None:
        print(f">>> {member.display_name} が {before.channel.name} から退場しました")
        vc_exit(member)

    # VC移動時（記録対象に関わらず）一旦退場してから入場するので...
    if before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
        print(f">>> {member.display_name} が {before.channel.name} から {after.channel.name} に移動しました")
        vc_exit(member) # 退場処理
        vc_join(member) # 入場処理



# ------------------------------
# ↑ イベントハンドラ
# ↓ スラッシュコマンド
# ------------------------------


# ■【1.】csvファイルの状況を確認するスラッシュコマンド
# - ファイルサイズ, データ数等を確認する
# - ファイルの最後のn行をtextで取得する
@tree.command(name="check-csv", description="csvファイルの状況を確認する")
async def check_csv(interaction: discord.Interaction, how_many: app_commands.Range[int, 1, 10] = 1):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=True) # 以降, interaction.followupを使う

    csv_metadata = check_csv_metadata(how_many)

    # ファイルサイズに応じたメッセージを作る 数十MBから100MB程度
    limit_byte = 100 * 1024 * 1024 # 100[Byte]
    percentage = (int(csv_metadata['size_byte']) / limit_byte) * 100 # [%]
    judge = ''
    if percentage < 70:
        judge += 'まだまだ大丈夫'
    elif percentage < 100:
        judge += 'そろそろヤバい'
    else:
        judge += 'デカいかも。csvファイルを交換してね'

    msg = "【csvファイル情報】\n"
    msg += f"- ファイルサイズ : {csv_metadata['size_byte']} Byte\n"
    msg += f"  → 100MBを100%としたとき : {percentage:.4f} %\n"
    msg += f"  → 判定 : 「{judge}」\n"
    msg += f"  → サイズは100MB以下推奨\n"
    msg += f"- データ数 : {csv_metadata['row_count']}\n"
    msg += f"- 直近の{how_many}データ : \n"
    msg += f"```{csv_metadata['last_n_rows']}```"

    await interaction.followup.send(msg)


# ■【2.】現在の経過時間を教えてくれるスラッシュコマンド（作業中のみ実行可能）
@tree.command(name="this", description="現在の経過時間を教えてくれる（作業中のみ）")
async def this(interaction: discord.Interaction):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=False)

    # 計測中にのみ使えるようにする
    if interaction.user.id in active_vc_user_state:
        this_time = formatTimeDelta(datetime.datetime.now() - active_vc_user_state[interaction.user.id]['start_time'])
        await interaction.followup.send(f"```作業開始してから {this_time} 経過中～```")
        return
    await interaction.followup.send(f"```計測中ではありません！```")


# ■【3.】今日の合計作業時間を教えてくれるスラッシュコマンド
@tree.command(name="today", description="今日の合計作業時間を教えてくれる")
async def today(interaction: discord.Interaction):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=False)

    # 計測中は使えないようにする
    if interaction.user.id in active_vc_user_state:
        await interaction.followup.send(f"```計測中です！ 作業を終了してから確認してね```")
        return
    today_total = sum_oneday(interaction.user.id, datetime.datetime.now())
    today_total = formatTimeDelta(today_total)
    await interaction.followup.send(f"```本日 {datetime.datetime.now().strftime(date_format)} の合計作業時間\n>>> {today_total} ```")


# ■【4.】昨日の合計作業時間を教えてくれるスラッシュコマンド（日付が変わってから就寝することが多いので実装）
@tree.command(name="yesterday", description="昨日の合計作業時間を教えてくれる")
async def yesterday(interaction: discord.Interaction):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=False)

    # 計測中は使えないようにする
    if interaction.user.id in active_vc_user_state:
        await interaction.followup.send(f"```計測中です！ 作業を終了してから確認してね```")
        return
    yesterday_total = sum_oneday(interaction.user.id, datetime.datetime.now() - datetime.timedelta(days=1))
    yesterday_total = formatTimeDelta(yesterday_total)
    await interaction.followup.send(
        f"```昨日 {(datetime.datetime.now() - datetime.timedelta(days=1)).strftime(date_format)} の合計作業時間\n>>> {yesterday_total} ```"
        )


# ■【5.】指定期間の合計作業時間を教えてくれるスラッシュコマンド
@tree.command(name="span", description="期間を指定して合計作業時間を計算する   西暦下2桁+月2桁+日2桁の形式で指定する   例) 2025年5月16日 なら 250516")
async def span(interaction: discord.Interaction, start_date: str, end_date: str):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=False)

    # 計測中は使えないようにする
    if interaction.user.id in active_vc_user_state:
        await interaction.followup.send(f"```計測中です！ 作業を終了してから確認してね```")
        return
    # バリデーション
    # start_dateとend_dateが正しい形式か確認
    try: # datetimeｵﾌﾞｼﾞｪｸﾄに変換できれば、日付形式として正しいとわかる
        start_date_dt = datetime.datetime.strptime(start_date, my_date_format)
        end_date_dt = datetime.datetime.strptime(end_date, my_date_format)
    except ValueError:
        await interaction.followup.send(f"```日付の形式が不正\n   正しい形式の例: 2025年5月16日 なら 250516```")
        return
    # start_dateとend_dateの大小関係を確認
    if start_date_dt > end_date_dt:
        await interaction.followup.send(f"```開始日付が終了日付よりも未来の日付になっています```")
        return
    # 期間内の合計作業時間を計算
    total_time = datetime.timedelta(0) # 変数用意
    # 期間内の日付を1日ずつループして合計作業時間を計算
    for i in range((end_date_dt - start_date_dt).days + 1):
        # 日付を取得
        target_date = start_date_dt + datetime.timedelta(days=i)
        # 合計作業時間を計算
        daily_total_time = sum_oneday(interaction.user.id, target_date)
        total_time += daily_total_time
        print(f'{str(target_date)}: {daily_total_time}')
    print(f"合計作業時間: {total_time}")
    await interaction.followup.send(
        f"```期間 {start_date_dt.strftime(date_format)} ~ {end_date_dt.strftime(date_format)} の合計作業時間\n>>> {formatTimeDelta(total_time)} ```"
        )


# ------------------------------
# ↑ スラッシュコマンド
# ↓ スケジューラにより自動で行われる関数
# ------------------------------


# ■【6.】週間報告 毎週月曜05:00に実行する関数
async def weekly_task(client: discord.Client):
    global summary_output_TCID
    try:
        now = datetime.datetime.now()
        # span_start = datetime.datetime(now.year, now.month, now.day, 0, 0, 0) - datetime.timedelta(weeks=1) # 先週の月曜日の0時
        # span_end = datetime.datetime(now.year, now.month, now.day, 0, 0, 0)
        span_end = datetime.datetime(now.year, now.month, now.day, 0, 0, 0) # 本日の日付
        span_end = span_end - datetime.timedelta(days=1) # 日付が変わった5時に送るので、前日を集計の最終日にする必要がある
        span_start = span_end - datetime.timedelta(days=6) # その1週間前の日付のため、weeks=1ではなくdays=6

        msg = f"## 週間報告   {span_start.strftime(my_date_format)} ~ {span_end.strftime(my_date_format)}\n"
        span_summary_by_user = sum_span() # 2段辞書型で帰ってくる

        # メッセージ整形
        # ユーザ毎にループ
        for user_id, user_data in span_summary_by_user.items():
            print(f"--- ユーザーID: {user_id} ---")

            msg += f"### {user_data['user_name']}\n"
            msg += "```\n"
            msg += f"カウント : {user_data['session_count']} 回\n"
            msg += f"合計時間 : {formatTimeDelta(user_data['total_time'])}\n"
            msg += "```\n"

        # 送信
        print(f'{msg}')
        target_channel = client.get_channel(int(summary_output_TCID))
        await target_channel.send(msg)
    except Exception as e:
        print(f"Error sending message: {e}")
        return


# ------------------------------
# ↑ スケジューラにより自動で行われる関数
# ↓ discordボットの初期化処理
# ------------------------------


@client.event
async def on_ready():
    # ------------------------------
    # ツリーコマンド動機
    try:
        guild = client.get_guild(os.getenv("DISCORD_GUILD_ID"))
        # まずギルドで同期できるか試す
        if guild:
            await tree.sync(guild=guild)
            print(f"Tree synced to guild: {guild.name} ({guild.id})")
        else:
            print("Guild not found. Syncing to global.")

    except Exception as e:
        print(f"Error syncing tree to guild : {e}")

    finally:
        # ギルドが見つからなかった場合はグローバルで同期
        await tree.sync()

    print('ATLogger is ready...')

    # ------------------------------
    # ■【6.】スケジューラの設定
    scheduler.add_job(
        weekly_task,              # 実行する非同期関数
        CronTrigger(              # cron形式のトリガーを使用
            day_of_week='mon',    # 曜日: 月曜日 (mon, tue, wed, thu, fri, sat, sun)
            hour=5,               # 時刻: 5時 (0-23)
            minute=0              # 時刻: 0分 (0-59)
        ),
        args=[client]             # 実行する関数に引数としてclientオブジェクトを渡す
        # id='weekly_monday_task', # ジョブにIDを付けると管理しやすくなります（任意）
        # replace_existing=True    # 同じIDのジョブがあれば置き換える（任意）
    )

    # ------------------------------
    # ■【6.】スケジューラを開始
    if not scheduler.running: # 多重起動防止
        scheduler.start()
        print("APScheduler 開始")
    else:
        print("APScheduler は既に起動")

# discordボット起動のトリガー
client.run(os.getenv("DISCORD_BOT_TOKEN"))