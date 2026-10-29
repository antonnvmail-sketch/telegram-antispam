"""Calendar-month operational counters in Moscow time, never model accuracy."""
import csv
from datetime import datetime,timedelta,timezone
import io

MSK=timezone(timedelta(hours=3))
MONTHS=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь']
FIELDS=['auto_deleted','manual_deleted','false_positive_reports','reviewed_ham']

def monthly_report(deletions,feedback,started_at,now=None):
    now=now or datetime.now(MSK)
    if now.tzinfo is None:raise ValueError('Timezone required')
    now=now.astimezone(MSK);rows=[];index=now.year*12+now.month-1
    for delta in [2,1,0]:
        n=index-delta;start=datetime(n//12,n%12+1,1,tzinfo=MSK)
        n+=1;end=datetime(n//12,n%12+1,1,tzinfo=MSK)
        available=started_at<=now.timestamp() and started_at<end.timestamp()
        lower=max(start.timestamp(),started_at);upper=min(end.timestamp(),now.timestamp())
        d=[r for r in deletions if lower<=r['at']<upper]
        f=[r for r in feedback if lower<=r['at']<upper]
        counts=dict(auto_deleted=sum(r['origin']=='auto' for r in d),manual_deleted=sum(r['origin']=='manual' for r in d),
                    false_positive_reports=sum(r['reason']=='false_positive_after_deletion' for r in f),
                    reviewed_ham=sum(r['label']=='HAM' and r['reason']=='explicit_review' for r in f))
        rows.append(dict(month=f'{start:%Y-%m}',title=f'{MONTHS[start.month-1]} {start.year}',
                         current=delta==0,collection_started_midmonth=available and started_at>start.timestamp(),
                         data_available=available,**{k:v if available else None for k,v in counts.items()}))
    return dict(timezone='Europe/Moscow',as_of=now.isoformat(),collection_started_at=datetime.fromtimestamp(started_at,MSK).isoformat(),
                months=rows,window='current_calendar_month_and_two_previous',is_model_accuracy=False,
                note='Ошибки — только явные отметки администраторов. Месяц ошибки определяется временем отметки; удаление — временем удаления. История начинается с включения счётчиков; непрерывность работы не измеряется.')

def format_report(report):
    lines=['Статистика по календарным месяцам · МСК',f"По состоянию на {datetime.fromisoformat(report['as_of']):%d.%m.%Y %H:%M}"]
    for row in report['months']:
        lines+=['',row['title']+(' — месяц не завершён' if row['current'] else ' — месяц завершён')]
        if not row['data_available']:lines.append('Сбор статистики ещё не был включён. Данных нет.');continue
        if row['collection_started_midmonth']:lines.append('Неполное покрытие: сбор начат '+datetime.fromisoformat(report['collection_started_at']).strftime('%d.%m.%Y %H:%M')+' МСК.')
        lines += [f"Бот удалил автоматически: {row['auto_deleted']}",f"Удалено по кнопке «Спам»: {row['manual_deleted']}",
                  f"Отмечено ошибок удаления: {row['false_positive_reports']}",f"Подозрение отклонено («Не спам»): {row['reviewed_ham']}"]
    return '\n'.join(lines)+'\n\n'+report['note']+' Это счётчики действий и ответов, а не общая точность модели.'

def csv_report(report):
    output=io.StringIO(newline='');writer=csv.writer(output,delimiter=';')
    writer.writerow(['Месяц','Удалено автоматически','Удалено по кнопке Спам','Отметок ошибки удаления','Подозрений отклонено','Данные доступны','Месяц не завершён','Сбор начат в середине месяца','Начало сбора МСК','Состояние на МСК'])
    for r in report['months']:writer.writerow([r['month'],*[r[k] for k in FIELDS],r['data_available'],r['current'],r['collection_started_midmonth'],report['collection_started_at'],report['as_of']])
    return output.getvalue().encode('utf-8-sig')
