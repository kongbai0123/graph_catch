"""Qt Chromium regression for model tabs, batch reports and chart scroll retention."""
from desktop_training import (QApplication,QWebEngineView,Page,QUrl,QEventLoop,QTimer,QTest,
    WorkbenchService,seed_reports,atomic_json,Image,Path,tempfile,json,time)
def main():
    app = QApplication.instance() or QApplication([])
    output=Path('qa-output/model-layout');output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vision-model-ui-') as directory:
        folder=Path(directory);service=WorkbenchService(folder/'data').start();pid,saved=seed_reports(service,folder)
        modelpath=service.training._model_path(pid,'M002');model=json.loads(modelpath.read_text(encoding='utf-8'));model['engine']='pixel_prototype_v1';model['test']={'images':2,'mean_iou':.8};atomic_json(modelpath,model)
        def comparison(project_id,model_id,split,progress):
            session='a'*32;dest=service.training.root/'model-trials'/session;media=dest/'media';media.mkdir(parents=True,exist_ok=True)
            frames=[]
            for i in range(6):
                Image.new('RGB',(640,360),(40,60,80)).save(media/f'{i}.png')
                truth={'type':'rectangle','label':'part','x':80,'y':80,'width':100,'height':100}
                prediction={**truth,'confidence':.9,'x':80 if i%2==0 else 300}
                frames.append({'image':f'{i}.png','width':640,'height':360,'frame_index':None,'shapes':[prediction],'ground_truth':[truth]})
            result={'session_id':session,'model_version_id':model_id,'frames':frames,'comparison':{'split':split,'iou_threshold':.5}}
            atomic_json(dest/'result.json',result);return result
        service.training.create_model_comparison=comparison
        view=QWebEngineView();page=Page(view);view.setPage(page);view.resize(1440,1000);view.show();view.setUrl(QUrl(service.entry_url));page.setVisible(True)
        downloads=[]
        def accept_download(item):
            downloads.append(item.downloadFileName());item.setDownloadDirectory(str(folder));item.accept()
        page.profile().downloadRequested.connect(accept_download)
        def js(script):
            result = []
            loop = QEventLoop()
            page.runJavaScript(script, lambda value: (result.append(value), loop.quit()))
            QTimer.singleShot(10000, loop.quit)
            loop.exec()
            assert result, "JavaScript timed out"
            return result[0]

        def wait(expression, seconds=30):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if js(expression):
                    return
                QTest.qWait(80)
            raise AssertionError("Timeout: " + expression + "\n" + str(js("document.body.innerText.slice(-4000)")))

        def click(selector):
            encoded = json.dumps(selector)
            wait(f"(()=>{{const e=document.querySelector({encoded});return e&&!e.disabled}})()")
            js(f"document.querySelector({encoded}).click()")


        try:
            wait("typeof window.workbenchState==='function'");click('.project-card-open');wait("!document.querySelector('[data-stage=models]').disabled")
            click('[data-stage=models]');wait("!!document.querySelector('#model-tab-overview')")
            for key in ['overview','settings','evaluation','comparison','trial']:
                click('#model-tab-'+key)
                assert js("document.querySelectorAll('#modelDetail > [role=tabpanel]:not([hidden])').length") == 1
                assert js("document.querySelector('#model-panel-"+key+"').getClientRects().length>0")
                QTest.qWait(200);view.grab().save(str(output/(key+'.png')))
            click('#modelList .model-row:nth-child(2)');click('#model-tab-comparison');click('#runModelComparison')
            wait("document.querySelectorAll('.batch-card').length===4")
            assert js("document.querySelectorAll('.batch-stage svg').length") == 8
            js("document.querySelector('#comparisonGallery select').value='fn';document.querySelector('#comparisonGallery select').dispatchEvent(new Event('change'))")
            assert js("document.querySelectorAll('.batch-card').length") == 3
            js("[...document.querySelectorAll('#comparisonGallery button')].find(b=>b.textContent.includes('PNG')).click()")
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and not list(folder.glob('M002-*.png')):QTest.qWait(100)
            png=next(folder.glob('M002-*.png'));assert Image.open(png).size==(1280,1300)
            js("[...document.querySelectorAll('#comparisonGallery button')].find(b=>b.textContent.includes('明細 JSON')).click()")
            deadline=time.monotonic()+10
            while time.monotonic()<deadline and not list(folder.glob('M002-*.json')):QTest.qWait(100)
            report=json.loads(next(folder.glob('M002-*.json')).read_text(encoding='utf-8'));assert report['summary']['tp']==3 and report['summary']['fn']==3
            QTest.qWait(200);view.grab().save(str(output/'batch.png'))
            click('#modelList .model-row:nth-child(1)')
            assert js("document.querySelector('#modelTrialViewer').hidden")
            assert js("document.querySelector('#comparisonGallery').hidden")
            click('[data-stage=train]');wait("!!document.querySelector('[data-chart-group=loss]')")
            js("document.querySelector('.training-chart-toolbar').scrollIntoView({block:'start'})");QTest.qWait(100)
            before=js("document.querySelector('.training-chart-toolbar').getBoundingClientRect().top")
            click('[data-chart-group=loss]');QTest.qWait(200)
            after=js("document.querySelector('.training-chart-toolbar').getBoundingClientRect().top")
            assert abs(before-after)<2,(before,after)
            click('[data-stage=models]');wait("!!document.querySelector('#model-tab-overview')")
            for width in [1024,760]:
                view.resize(width,1000);click('#model-tab-settings');QTest.qWait(200)
                assert not js('document.documentElement.scrollWidth>innerWidth+2')
                view.grab().save(str(output/('settings-'+str(width)+'.png')))
            js("document.querySelector('#modelSearch').value='no-matching-model';document.querySelector('#modelSearch').dispatchEvent(new Event('input'))")
            assert js("document.querySelector('#modelDetail').innerText.includes('尚無可用模型')")
            click('#clearModelFilters');wait("!!document.querySelector('#model-tab-overview')")
            assert not page.errors,page.errors
            print('MODEL_LAYOUT_OK',flush=True)
        finally:
            view.close();service.close()
if __name__=='__main__':main()
