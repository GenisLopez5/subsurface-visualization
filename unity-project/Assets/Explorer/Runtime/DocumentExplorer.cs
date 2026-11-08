using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Subsurface.Borehole;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UIElements;

namespace Subsurface.Explorer
{
    public sealed partial class DocumentExplorer : MonoBehaviour
    {
        public string apiBase="http://100.107.190.65:8000";
        public BoreholeViewer viewer;
        public UIDocument document;
        public StyleSheet stylesheet;
        [Serializable]public class Entry {public string id,name,status,error,created_at,updated_at;}
        [Serializable]class DeleteResult {public string[] deleted,not_found;}
        [Serializable]class Listing {public int total;public Entry[] items;}
        [Serializable]class Rejected {public string filename,reason;}
        [Serializable]class UploadResult {public Entry[] files;public Rejected[] rejected;}
        [Serializable]class StatusList {public Entry[] items;}
        [Serializable]class Tracking {public List<Entry> items=new List<Entry>();}
        readonly List<Entry> files=new List<Entry>();
        readonly HashSet<string> selected=new HashSet<string>();
        Tracking tracking=new Tracking();
        VisualElement root,page,activity,activityBody;
        ScrollView rows;
        Label message,selection,activityTitle;
        TextField search;
        VisualElement contextMenu,confirmation;
        Button downloadAll,activityToggle,activityClose,searchClear;
        ExplorerSpinner feedbackSpinner;VisualElement busyOverlay;Label busyFile,searchPlaceholder;string openingName;bool listing;
        Texture2D activeImage;
        int anchor=-1,revision,openRevision;
        bool uploading,opening,downloading,descending,deleting,visualizing,activityDismissed,activityCollapsed;
        string trackingKey=>"Subsurface.Uploads."+apiBase;
        public int FileCount=>files.Count;
        public string Message=>message?.text;
        void Start()
        {
            apiBase=PlayerPrefs.GetString("Subsurface.ApiHost",apiBase);root=document.rootVisualElement;root.pickingMode=PickingMode.Ignore;root.Clear();if(stylesheet!=null)root.styleSheets.Add(stylesheet);
            page=new VisualElement();page.AddToClassList("explorer");root.Add(page);
            var bar=Line(page,"toolbar");var identity=new VisualElement();identity.AddToClassList("identity");bar.Add(identity);var brand=new Label("SUBSURFACE");brand.AddToClassList("eyebrow");identity.Add(brand);var title=new Label("Documents");title.AddToClassList("brand");identity.Add(title);
            var searchShell=Line(bar,"search-shell");var icon=new VisualElement();icon.AddToClassList("search-icon");icon.pickingMode=PickingMode.Ignore;var handle=new VisualElement();handle.AddToClassList("search-handle");icon.Add(handle);searchShell.Add(icon);
            search=new TextField{name="documentSearch"};search.AddToClassList("search");searchShell.Add(search);
            searchPlaceholder=new Label("Search documents"){pickingMode=PickingMode.Ignore};searchPlaceholder.AddToClassList("search-placeholder");searchShell.Add(searchPlaceholder);
            searchClear=AddButton(searchShell,"×",()=>search.value="","search-clear");searchClear.tooltip="Clear search";searchClear.style.visibility=Visibility.Hidden;
            searchShell.RegisterCallback<FocusInEvent>(_=>searchShell.AddToClassList("focused"));searchShell.RegisterCallback<FocusOutEvent>(_=>searchShell.RemoveFromClassList("focused"));
            search.RegisterValueChangedCallback(_=>{bool empty=string.IsNullOrEmpty(search.value);searchPlaceholder.style.display=empty?DisplayStyle.Flex:DisplayStyle.None;searchClear.style.visibility=empty?Visibility.Hidden:Visibility.Visible;revision++;CancelInvoke(nameof(Refresh));Invoke(nameof(Refresh),.35f);});
            AddButton(bar,"Refresh",Refresh);AddButton(bar,"Upload TIFFs",ChooseUpload,"primary");
            var actions=Line(page,"actions");selection=new Label("Select documents");selection.AddToClassList("grow");actions.Add(selection);
            
            CreateHostControls(actions,"explorerHost");downloadAll=AddButton(actions,"Download all",()=>Download(true));
            var fileCard=new VisualElement();fileCard.AddToClassList("file-card");page.Add(fileCard);var header=Line(fileCard,"file-header");AddButton(header,"Name ↕",()=>{descending=!descending;Sort();Render();},"name");
            rows=new ScrollView();rows.AddToClassList("file-list");fileCard.Add(rows);
            var feedback=Line(page,"feedback");feedbackSpinner=new ExplorerSpinner();feedback.Add(feedbackSpinner);message=new Label();message.AddToClassList("message");feedback.Add(message);
            busyOverlay=new VisualElement{name="openingOverlay"};busyOverlay.AddToClassList("opening-overlay");var busyCard=new VisualElement();busyCard.AddToClassList("opening-card");busyOverlay.Add(busyCard);var largeSpinner=new ExplorerSpinner();largeSpinner.AddToClassList("spinner-large");busyCard.Add(largeSpinner);var busyTitle=new Label("Opening document");busyTitle.AddToClassList("opening-title");busyCard.Add(busyTitle);busyFile=new Label();busyFile.enableRichText=false;busyFile.AddToClassList("opening-file");busyCard.Add(busyFile);busyOverlay.style.display=DisplayStyle.None;page.Add(busyOverlay);
            activity=new VisualElement();activity.AddToClassList("activity");root.Add(activity);var activityHeader=Line(activity,"activity-header");activityTitle=new Label("Uploads");activityTitle.AddToClassList("grow");activityHeader.Add(activityTitle);
            activityBody=new ScrollView();activityBody.AddToClassList("activity-body");activity.Add(activityBody);
            activityToggle=AddButton(activityHeader,"",()=>{activityCollapsed=!activityCollapsed;RenderActivity();},"activity-toggle");var chevron=new VisualElement();chevron.AddToClassList("activity-chevron");activityToggle.Add(chevron);
            activityClose=AddButton(activityHeader,"×",CloseActivity,"activity-close");activityClose.tooltip="Dismiss completed uploads";
            PlayerPrefs.DeleteKey(trackingKey);
            root.RegisterCallback<PointerDownEvent>(e=>{if(contextMenu!=null&&!contextMenu.worldBound.Contains(e.position))CloseContextMenu();},TrickleDown.TrickleDown);
            root.RegisterCallback<KeyDownEvent>(e=>{if(e.keyCode==KeyCode.Escape){CloseContextMenu();confirmation?.RemoveFromHierarchy();confirmation=null;}});
            ShowLibrary();RenderActivity();StartCoroutine(ConnectHost(apiBase));StartCoroutine(Poll());
        }
        static VisualElement Line(VisualElement parent,string cls){var line=new VisualElement();line.AddToClassList(cls);parent.Add(line);return line;}
        static Button AddButton(VisualElement parent,string text,Action action,string cls=null){var button=new Button(action){text=text};if(cls!=null)button.AddToClassList(cls);parent.Add(button);return button;}
        string Url(string route)=>apiBase.TrimEnd('/')+route;
        void Say(string text)
        {
            message.text=text;message.EnableInClassList("message-error",text.IndexOf("failed",StringComparison.OrdinalIgnoreCase)>=0||text.StartsWith("Cannot")||text.Contains("not available"));UpdateFeedback();
        }
        void UpdateFeedback()
        {
            if(feedbackSpinner==null)return;feedbackSpinner.style.display=listing||opening||downloading||deleting?DisplayStyle.Flex:DisplayStyle.None;
            busyOverlay.style.display=opening&&!visualizing?DisplayStyle.Flex:DisplayStyle.None;busyFile.text=(openingName??"")+"\n"+(opening?message.text:"");
        }
        public void Refresh(){CloseContextMenu();StartCoroutine(ListFiles(++revision));}
        IEnumerator ListFiles(int version)
        {
            listing=true;UpdateFeedback();
            try
            {
            Say("Loading documents…");var loaded=new List<Entry>();int total;
            do
            {
                using(var request=UnityWebRequest.Get(Url("/api/v1/files?status=done&limit=1000&offset="+loaded.Count+"&q="+UnityWebRequest.EscapeURL(search.value??""))))
                {
                    request.timeout=30;yield return request.SendWebRequest();if(version!=revision)yield break;
                    if(request.result!=UnityWebRequest.Result.Success){Say("Cannot load documents: "+request.error+". Use Refresh to retry.");yield break;}
                    Listing result=null;try{result=JsonUtility.FromJson<Listing>(request.downloadHandler.text);}catch(Exception e){Say(e.Message);}if(result==null||result.items==null)yield break;
                    loaded.AddRange(result.items);total=result.total;if(result.items.Length==0)break;
                }
            }while(loaded.Count<total);
            files.Clear();files.AddRange(loaded);selected.IntersectWith(files.Select(x=>x.id));anchor=-1;Sort();Render();Say(files.Count==0?(string.IsNullOrEmpty(search.value)?"Upload TIFFs to get started.":"No documents match your search."):"");
            }
            finally{if(version==revision){listing=false;UpdateFeedback();}}
        }
        void Sort(){files.Sort((a,b)=>(descending?-1:1)*StringComparer.OrdinalIgnoreCase.Compare(a.name,b.name));anchor=-1;}
        void Render()
        {
            rows.Clear();for(int i=0;i<files.Count;i++)
            {
                int index=i;var file=files[i];var row=new VisualElement();row.AddToClassList("file-row");row.EnableInClassList("selected",selected.Contains(file.id));
                var name=new Label(file.name);name.enableRichText=false;name.tooltip=file.name;name.AddToClassList("name");row.Add(name);rows.Add(row);
                row.RegisterCallback<PointerDownEvent>(e=>{if(e.button==1){if(!selected.Contains(file.id))Select(index,false,false);ShowContextMenu(e.position);e.StopPropagation();return;}if(e.button!=0)return;CloseContextMenu();Select(index,e.shiftKey,e.ctrlKey||e.commandKey);if(e.clickCount==2&&!e.shiftKey&&!e.ctrlKey&&!e.commandKey)Open(file);});
            }
            selection.text=selected.Count==0?files.Count+" documents":selected.Count+" selected";
        }
        public void Select(int index,bool shift,bool control)
        {
            if(index<0||index>=files.Count)return;
            if(shift&&anchor>=0){if(!control)selected.Clear();for(int i=Math.Min(anchor,index);i<=Math.Max(anchor,index);i++)selected.Add(files[i].id);}
            else{if(!control)selected.Clear();if(control&&selected.Contains(files[index].id))selected.Remove(files[index].id);else selected.Add(files[index].id);anchor=index;}
            // Keep rows in place so native double-click detection remains intact.
            for(int i=0;i<rows.contentContainer.childCount;i++)rows.contentContainer[i].EnableInClassList("selected",selected.Contains(files[i].id));
            selection.text=selected.Count+" selected";
        }
        public void ShowLibrary(){visualizing=false;RenderActivity();CloseContextMenu();openRevision++;page.style.display=DisplayStyle.Flex;viewer.Root.style.display=DisplayStyle.None;}
        public void Open(Entry file){CloseContextMenu();if(opening||deleting||connecting)return;StartCoroutine(OpenFile(file,++openRevision));}
        IEnumerator OpenFile(Entry file,int version)
        {
            var timer=System.Diagnostics.Stopwatch.StartNew();double downloadMs=0,prepareMs=0;System.Threading.Tasks.Task pendingWork=null;
            opening=true;openingName=file.name;Say("Opening "+file.name+"…");byte[] originalLegend=null,generatedLegend=null;string csv=null;double displacement=0;string temp=Path.Combine(Application.temporaryCachePath,Guid.NewGuid()+".tif"),archive=temp+".zip";
            try
            {
                using(var request=UnityWebRequest.Get(Url("/api/v1/files/"+file.id+"/result?format=zip&include_source=true")))
                {
                    request.downloadHandler=new DownloadHandlerFile(archive){removeFileOnAbort=true};request.timeout=180;var operation=request.SendWebRequest();while(!operation.isDone){Say("Downloading result · "+(request.downloadedBytes/1048576d).ToString("0.0",CultureInfo.InvariantCulture)+" MB");yield return null;}downloadMs=timer.Elapsed.TotalMilliseconds;if(request.result!=UnityWebRequest.Result.Success){Say(request.responseCode==404?"This document's result is not available yet. Refresh and try again.":"Result download failed: "+request.error);yield break;}
                }
                if(version!=openRevision)yield break;
                Say("Preparing document preview…");yield return null;BoreholeDocumentImage image=null;
                var work=System.Threading.Tasks.Task.Run(()=>
                {
                    var preparation=System.Diagnostics.Stopwatch.StartNew();
                    var original=DocumentResultArchive.ReadLegend(archive,true);var generated=DocumentResultArchive.ReadLegend(archive,false);
                    string content=DocumentResultArchive.Read(archive,temp,out var offset);var pixels=BoreholeDocumentImage.ReadPreview(temp);
                    if((offset.result_width>0&&offset.result_width!=pixels.OriginalWidth)||(offset.result_height>0&&offset.result_height!=pixels.OriginalHeight))throw new IOException("The result image dimensions disagree with its offset metadata.");
                    return (original,generated,content,offset,pixels,preparation.Elapsed.TotalMilliseconds);
                });
                pendingWork=work;while(!work.IsCompleted)yield return null;
                if(version!=openRevision){if(work.IsFaulted)Debug.LogWarning(work.Exception.GetBaseException().Message);yield break;}
                try
                {
                    var prepared=work.GetAwaiter().GetResult();originalLegend=prepared.original;generatedLegend=prepared.generated;csv=prepared.content;displacement=prepared.offset.offset_y_px;prepareMs=prepared.Item6;var offset=prepared.offset;image=new BoreholeDocumentImage{Texture=prepared.pixels.CreateTexture(),OriginalWidth=prepared.pixels.OriginalWidth,OriginalHeight=prepared.pixels.OriginalHeight};
                    if((offset.result_width>0&&offset.result_width!=image.OriginalWidth)||(offset.result_height>0&&offset.result_height!=image.OriginalHeight))throw new IOException("The result image dimensions disagree with its offset metadata.");
                }
                catch(Exception e){if(image!=null)Destroy(image.Texture);Say("Cannot open document result: "+e.Message);yield break;}
                if(!viewer.SetAnalysisResult(csv,image.Texture,file.name,image.OriginalWidth,image.OriginalHeight,displacement,temp)){Destroy(image.Texture);Say(viewer.LastError);yield break;}
                string source=null;try{source=DocumentResultArchive.ReadSource(archive,temp+".source");if(source!=null)viewer.SetAnnotationSource(source);}catch(Exception ex){Debug.LogWarning("Unannotated source unavailable: "+ex.Message);}finally{if(source!=null)File.Delete(source);}
                viewer.SetLegends(originalLegend,generatedLegend);if(activeImage!=null)Destroy(activeImage);activeImage=image.Texture;viewer.Root.style.display=DisplayStyle.Flex;page.style.display=DisplayStyle.None;visualizing=true;ClearFinishedUploads();RenderActivity();
                var header=viewer.Root.Q(className:"header");var back=viewer.Root.Q<Button>("backToDocuments");if(back==null){back=new Button(ShowLibrary){name="backToDocuments",text="← Documents"};back.AddToClassList("header-button");header.Insert(0,back);}
                yield return null;Debug.Log("DOCUMENT_LOAD "+file.id+" download_ms="+downloadMs.ToString("0.0",CultureInfo.InvariantCulture)+" prepare_ms="+prepareMs.ToString("0.0",CultureInfo.InvariantCulture)+" first_usable_ms="+timer.Elapsed.TotalMilliseconds.ToString("0.0",CultureInfo.InvariantCulture));Say("");
            }
            finally{Action cleanup=()=>{try{if(File.Exists(temp))File.Delete(temp);if(File.Exists(archive))File.Delete(archive);}catch(IOException){}catch(UnauthorizedAccessException){}};if(pendingWork!=null&&!pendingWork.IsCompleted)pendingWork.ContinueWith(t=>{if(t.IsFaulted){var observed=t.Exception;}cleanup();});else cleanup();opening=false;UpdateFeedback();}
        }
        void ChooseUpload()
        {
            if(connecting){Say("Wait for the connection check to finish.");return;}
            if(uploading){Say("Wait for the current upload transfer to finish.");return;}
            try{var paths=ExplorerDialogs.Pick();if(paths.Length>0)StartCoroutine(Upload(paths));}catch(Exception e){Say(e.Message);}
        }
        IEnumerator Upload(string[] paths)
        {
            uploading=true;activityDismissed=false;var pending=new Entry{name=paths.Length+" TIFF document(s)",status="Preparing upload"};tracking.items.Add(pending);RenderActivity();
            try
            {
                var form=new List<IMultipartFormSection>();string failure=null;long bytes=0;
                try{foreach(string path in paths){string ext=Path.GetExtension(path).ToLowerInvariant();if(ext!=".tif"&&ext!=".tiff")throw new IOException("Choose TIFF files only.");bytes+=new FileInfo(path).Length;if(bytes>512L*1024*1024)throw new IOException("Choose a smaller batch (512 MB maximum per transfer).");form.Add(new MultipartFormFileSection("files",File.ReadAllBytes(path),Path.GetFileName(path),"image/tiff"));}}catch(Exception e){failure=e.Message;}
                if(failure!=null){pending.status="error";pending.error=failure;yield break;}
                using(var request=UnityWebRequest.Post(Url("/api/v1/uploads"),form))
                {
                    request.timeout=600;var operation=request.SendWebRequest();while(!operation.isDone){pending.status="Uploading "+Mathf.RoundToInt(request.uploadProgress*100)+"%";RenderActivity();yield return new WaitForSecondsRealtime(.25f);}
                    if(request.result!=UnityWebRequest.Result.Success){pending.status="error";pending.error=request.error;yield break;}
                    UploadResult result=null;try{result=JsonUtility.FromJson<UploadResult>(request.downloadHandler.text);}catch(Exception e){pending.status="error";pending.error=e.Message;}
                    if(result==null)yield break;tracking.items.Remove(pending);if(result.files!=null)foreach(var entry in result.files){tracking.items.RemoveAll(x=>x.id==entry.id);tracking.items.Add(entry);}if(result.rejected!=null)foreach(var rejected in result.rejected)tracking.items.Add(new Entry{name=rejected.filename,status="error",error=rejected.reason});
                }
                Refresh();
            }
            finally{uploading=false;RenderActivity();}
        }
        static bool Pending(Entry e)=>!string.IsNullOrEmpty(e.id)&&(e.status=="queued"||e.status=="processing"||e.status=="uploading");
        IEnumerator Poll()
        {
            while(true)
            {
                var pending=tracking.items.Where(Pending).ToArray();bool completed=false;
                for(int offset=0;offset<pending.Length;offset+=50)
                {
                    string ids=string.Join(",",pending.Skip(offset).Take(50).Select(x=>x.id));
                    using(var request=UnityWebRequest.Get(Url("/api/v1/status?ids="+ids)))
                    {
                        request.timeout=20;yield return request.SendWebRequest();if(request.result!=UnityWebRequest.Result.Success){activityTitle.text="Uploads · reconnecting…";continue;}
                        StatusList result=null;try{result=JsonUtility.FromJson<StatusList>("{\"items\":"+request.downloadHandler.text+"}");}catch{}if(result?.items==null)continue;
                        foreach(var item in result.items){var entry=tracking.items.Find(x=>x.id==item.id);if(entry==null)continue;completed|=item.status=="done"&&entry.status!="done";entry.status=item.status;entry.error=item.error;}
                    }
                }
                if(pending.Length>0){RenderActivity();}if(completed)Refresh();yield return new WaitForSecondsRealtime(3);
            }
        }
        
        static bool ActiveUpload(Entry entry)=>Pending(entry)||(string.IsNullOrEmpty(entry.id)&&(entry.status=="Preparing upload"||(entry.status??"").StartsWith("Uploading")));
        void ClearFinishedUploads(){tracking.items.RemoveAll(x=>!ActiveUpload(x));}
        void CloseActivity(){if(uploading||tracking.items.Any(ActiveUpload))return;ClearFinishedUploads();activityDismissed=true;RenderActivity();}
        void RenderActivity()
        {
            if(visualizing)ClearFinishedUploads();
            activity.style.display=visualizing||activityDismissed||tracking.items.Count==0?DisplayStyle.None:DisplayStyle.Flex;activityTitle.text="Uploads · "+tracking.items.Count(ActiveUpload)+" active";activityBody.style.display=activityCollapsed?DisplayStyle.None:DisplayStyle.Flex;activityToggle.EnableInClassList("collapsed",activityCollapsed);activityToggle.tooltip=activityCollapsed?"Expand uploads":"Collapse uploads";activityClose.SetEnabled(!uploading&&!tracking.items.Any(ActiveUpload));activityBody.Clear();
            foreach(var entry in tracking.items)
            {
                var item=new VisualElement();item.AddToClassList("upload-entry");var name=new Label(entry.name);name.enableRichText=false;name.AddToClassList("upload-name");item.Add(name);item.Add(StatusBadge(entry.status));
                if(!string.IsNullOrEmpty(entry.error)){var error=new Label(entry.error);error.enableRichText=false;error.AddToClassList("upload-error");item.Add(error);}
                if((entry.status??"").StartsWith("Uploading ")&&float.TryParse(entry.status.Substring(10).TrimEnd('%'),NumberStyles.Float,CultureInfo.InvariantCulture,out var percent))
                {var track=new VisualElement();track.AddToClassList("upload-progress");var fill=new VisualElement();fill.AddToClassList("upload-progress-fill");fill.style.width=Length.Percent(Mathf.Clamp(percent,0,100));track.Add(fill);item.Add(track);}
                activityBody.Add(item);
            }
        }
        static VisualElement StatusBadge(string status)
        {
            status=status??"";bool transfer=status.StartsWith("Uploading")||status=="uploading"||status=="Preparing upload";bool processing=status=="processing",queued=status=="queued",done=status=="done",error=status=="error";
            var badge=new VisualElement();badge.AddToClassList("status-pill");badge.AddToClassList(transfer?"status-uploading":processing?"status-processing":queued?"status-queued":done?"status-done":error?"status-error":"status-attention");
            if(transfer||processing||queued)badge.Add(new ExplorerSpinner());else{var dot=new VisualElement();dot.AddToClassList("status-dot");badge.Add(dot);}
            string text=transfer?(status=="uploading"?"Uploading":status):processing?"Processing":queued?"Queued":done?"Complete":error?"Failed":status=="needs_annotation"?"Needs annotation":status;
            var label=new Label(text);label.AddToClassList("status-text");badge.Add(label);
            if(processing)label.schedule.Execute(()=>label.style.opacity=.65f+.35f*(.5f+.5f*Mathf.Sin(Time.realtimeSinceStartup*3))).Every(50);
            return badge;
        }
        void Download(bool all)
        {
            if(downloading||connecting)return;string[] ids=selected.ToArray();if(!all&&ids.Length==0)return;
            try{string path=ExplorerDialogs.Save(all?"all-documents.zip":"documents.zip");if(!string.IsNullOrEmpty(path))StartCoroutine(DownloadZip(path,all?"/api/v1/download/all":ids.Length==1?"/api/v1/files/"+ids[0]+"/download":"/api/v1/download?ids="+string.Join(",",ids)));}catch(Exception e){Say(e.Message);}
        }
        IEnumerator DownloadZip(string destination,string route)
        {
            downloading=true;downloadAll.SetEnabled(false);string temp=destination+"."+Guid.NewGuid()+".part";
            try
            {
                using(var request=UnityWebRequest.Get(Url(route)))
                {
                    request.downloadHandler=new DownloadHandlerFile(temp){removeFileOnAbort=true};request.timeout=600;var operation=request.SendWebRequest();while(!operation.isDone){Say(request.downloadProgress>0?"Downloading ZIP · "+Mathf.RoundToInt(request.downloadProgress*100)+"%":"Preparing ZIP…");yield return null;}
                    if(request.result!=UnityWebRequest.Result.Success){Say("Download failed: "+request.error);yield break;}
                }
                string failure=null;try{if(File.Exists(destination))File.Replace(temp,destination,null);else File.Move(temp,destination);}catch(Exception e){failure=e.Message;}Say(failure??"Saved ZIP to "+destination);
            }
            finally{if(File.Exists(temp))File.Delete(temp);downloading=false;downloadAll.SetEnabled(true);UpdateFeedback();}
        }
        void CloseContextMenu(){contextMenu?.RemoveFromHierarchy();contextMenu=null;}
        public void ShowContextMenu(Vector2 position)
        {
            CloseContextMenu();contextMenu=new VisualElement{name="documentContextMenu",focusable=true};contextMenu.AddToClassList("context-menu");root.Add(contextMenu);
            var local=root.WorldToLocal(position);contextMenu.style.left=Mathf.Clamp(local.x,0,Mathf.Max(0,root.contentRect.width-220));contextMenu.style.top=Mathf.Clamp(local.y,0,Mathf.Max(0,root.contentRect.height-100));
            var downloadItem=AddButton(contextMenu,selected.Count==1?"Download ZIP":"Download "+selected.Count+" documents",()=>{CloseContextMenu();Download(false);});downloadItem.SetEnabled(selected.Count>0&&!downloading);
            var deleteItem=AddButton(contextMenu,selected.Count==1?"Delete document":"Delete "+selected.Count+" documents",ConfirmDelete,"danger");deleteItem.SetEnabled(selected.Count>0&&!deleting&&!opening);contextMenu.Focus();
        }
        void ConfirmDelete()
        {
            CloseContextMenu();if(deleting||connecting||selected.Count==0)return;string[] ids=selected.ToArray();var names=files.Where(x=>selected.Contains(x.id)).Select(x=>x.name).ToArray();
            confirmation=new VisualElement{name="deleteConfirmation"};confirmation.AddToClassList("modal-overlay");root.Add(confirmation);var card=new VisualElement();card.AddToClassList("confirm-card");confirmation.Add(card);
            var title=new Label("Delete "+ids.Length+" document"+(ids.Length==1?"?":"s?"));title.AddToClassList("confirm-title");card.Add(title);
            var summary=new Label(string.Join("\n",names.Take(5))+(names.Length>5?"\n+ "+(names.Length-5)+" more":""));summary.enableRichText=false;summary.AddToClassList("confirm-copy");card.Add(summary);
            var description=new Label("The original files, annotations, and processed results will be permanently deleted from the server.");description.AddToClassList("confirm-copy");card.Add(description);var buttons=Line(card,"confirm-actions");
            AddButton(buttons,"Cancel",()=>{confirmation.RemoveFromHierarchy();confirmation=null;});AddButton(buttons,"Delete",()=>{confirmation.RemoveFromHierarchy();confirmation=null;StartCoroutine(DeleteFiles(ids));},"danger");
        }
        IEnumerator DeleteFiles(string[] ids)
        {
            if(deleting||connecting)yield break;deleting=true;Say("Deleting documents…");
            try
            {
                using(var request=UnityWebRequest.Delete(Url("/api/v1/files?ids="+UnityWebRequest.EscapeURL(string.Join(",",ids)))))
                {
                    request.downloadHandler=new DownloadHandlerBuffer();request.timeout=60;yield return request.SendWebRequest();
                    if(request.result!=UnityWebRequest.Result.Success){Say("Delete failed: "+request.error+". Your selection has been kept.");yield break;}
                    DeleteResult result=null;try{result=JsonUtility.FromJson<DeleteResult>(request.downloadHandler.text);}catch(Exception e){Say("Cannot read delete response: "+e.Message);}if(result==null)yield break;
                    var removed=new HashSet<string>((result.deleted??Array.Empty<string>()).Concat(result.not_found??Array.Empty<string>()));
                    tracking.items.RemoveAll(x=>removed.Contains(x.id));RenderActivity();selected.ExceptWith(removed);
                    yield return ListFiles(++revision);Say((result.deleted?.Length??0)+" document(s) deleted"+((result.not_found?.Length??0)>0?" · "+result.not_found.Length+" already absent":""));
                }
            }
            finally{deleting=false;UpdateFeedback();}
        }
        void OnDestroy(){StopAllCoroutines();if(activeImage!=null)Destroy(activeImage);}
    }
}
