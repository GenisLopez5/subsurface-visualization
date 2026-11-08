using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using Subsurface.Lithology;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UIElements;

namespace Subsurface.Borehole
{
    [DefaultExecutionOrder(-10000),RequireComponent(typeof(UIDocument))]
    public sealed class BoreholeViewer : MonoBehaviour
    {
        public BoreholeColumn column;
        public Camera columnCamera;
        public Camera presentationCamera;
        public TextAsset initialCsv;
        public Texture2D initialDocumentImage;
        public bool isolateOtherScenes;
        public string initialSourceName="example.csv";
        public double FocusDepthFeet {get;private set;}
        public double WindowFeet {get;private set;}
        public bool DocumentMode => false;
        public bool ShowingSurface => column.Document!=null&&((documentPane!=null&&documentPane.AtHeader)||FocusDepthFeet<=column.Document.TopFeet);
        public bool DocumentSynchronized => documentPane!=null&&documentPane.IsSynchronized;
        public double VisibleTopFeet => FocusDepthFeet-WindowFeet/(2*Math.Cos(pitch*Math.PI/180));
        public double VisibleBottomFeet => FocusDepthFeet+WindowFeet/(2*Math.Cos(pitch*Math.PI/180));
        public string LastError {get;private set;}
        public int AdvancingFrames {get;private set;}
        public string SourceName {get;private set;}
        public Texture2D SegmentedImage {get;private set;}
        bool annotationsVisible=true;BoreholeDocumentImage cleanPage;string cleanPath,annotatedPath;Texture2D annotatedTexture;int annotatedWidth,annotatedHeight;double annotatedOffset;
        public void SetAnnotationSource(string path)
        {
            annotatedPath=FullResolutionDocumentPath;annotatedTexture=SegmentedImage;annotatedWidth=DocumentPixelWidth;annotatedHeight=DocumentPixelHeight;annotatedOffset=PixelDisplacement;
            try
            {
                cleanPage=BoreholeDocumentImage.Load(path);cleanPath=Path.Combine(Application.temporaryCachePath,"source-"+Guid.NewGuid()+Path.GetExtension(path));File.Copy(path,cleanPath);
                annotatedPath=FullResolutionDocumentPath;annotatedTexture=SegmentedImage;annotatedWidth=DocumentPixelWidth;annotatedHeight=DocumentPixelHeight;annotatedOffset=PixelDisplacement;
                var button=root.Q<Button>("toggleAnnotations");button.SetEnabled(true);button.tooltip="Show or hide generated annotations; the green interval selection stays visible";ApplyAnnotations();
            }
            catch(Exception ex){ClearAnnotationSource();root.Q<Button>("toggleAnnotations").tooltip="Source image unavailable: "+ex.Message;}
        }
        void ApplyAnnotations()
        {
            if(cleanPage==null)return;double previousOffset=PixelDisplacement;documentPane?.ClearTiles();
            SegmentedImage=annotationsVisible?annotatedTexture:cleanPage.Texture;FullResolutionDocumentPath=annotationsVisible?annotatedPath:cleanPath;
            DocumentPixelWidth=annotationsVisible?annotatedWidth:cleanPage.OriginalWidth;DocumentPixelHeight=annotationsVisible?annotatedHeight:cleanPage.OriginalHeight;PixelDisplacement=annotationsVisible?annotatedOffset:0;
            root.Q<Button>("toggleAnnotations").text=annotationsVisible?"Annotations: on":"Annotations: off";documentPane?.ReplacePage(column.Document,SegmentedImage,DocumentPixelWidth,DocumentPixelHeight,previousOffset);
        }
        void ClearAnnotationSource()
        {
            if(cleanPage!=null){var pending=documentPane?.PendingDocumentWork;documentPane?.ClearTiles();FullResolutionDocumentPath=annotatedPath;SegmentedImage=annotatedTexture;DocumentPixelWidth=annotatedWidth;DocumentPixelHeight=annotatedHeight;PixelDisplacement=annotatedOffset;var texture=cleanPage.Texture;if(Application.isPlaying)Destroy(texture);else DestroyImmediate(texture);string path=cleanPath;if(pending!=null&&!pending.IsCompleted)pending.ContinueWith(_=>DeleteDocumentSource(path));else DeleteDocumentSource(path);}
            cleanPage=null;cleanPath=null;var button=root?.Q<Button>("toggleAnnotations");if(button!=null){button.SetEnabled(false);button.text="Annotations: on";}
        }
        public double PixelDisplacement {get;private set;}
        public string FullResolutionDocumentPath {get;private set;}
        public int DocumentDetailTileCount=>documentPane?.Tiles.ResidentTiles??0;
        public bool DocumentDetailReady=>documentPane!=null&&documentPane.Tiles.Ready;
        public int DocumentSourcePixelsPerTexel=>documentPane?.Tiles.SourcePixelsPerTexel??1;
        public int DocumentPixelWidth {get;private set;}
        public int DocumentPixelHeight {get;private set;}
        VisualElement root,viewport,ruler,labelLayer;
        Image renderImage;
        Label filename,range,error,status,selectedName,selectedDepths,selectedInfo,depthReadout;
        BoreholeDocumentPane documentPane;
        BoreholeCsvOverlay csvOverlay;
        BoreholeCutaway cutaway;
        Texture2D ownedImage;
        readonly List<Label> ticks=new List<Label>();
        readonly List<Button> intervalLabels=new List<Button>();
        readonly List<VisualElement> intervalConnectors=new List<VisualElement>();
        readonly List<GameObject> mutedRoots=new List<GameObject>();
        RenderTexture texture;
        const float yaw=15,pitch=7;
        bool poseDirty=true,dragging,moved,initializationPending;
        int pointerId;
        Vector2 pointerStart,pointerPrevious;
        BoreholeLegends legends;
        public void SetLegends(byte[] original,byte[] generated)=>legends?.Bind(original,generated);
        Slider zoomSlider;Label zoomPercent;
        DisplayMode displayMode;
        public VisualElement Root => root;
        void OnEnable()
        {
            if(column==null||columnCamera==null)return;
            IsolateOtherScenes();InitializeWhenReady();
        }
        void IsolateOtherScenes()
        {
            if(isolateOtherScenes)for(int i=0;i<SceneManager.sceneCount;i++)
            {
                var scene=SceneManager.GetSceneAt(i);if(scene==gameObject.scene||!scene.isLoaded)continue;
                foreach(var go in scene.GetRootGameObjects())if(go.activeSelf){if(!mutedRoots.Contains(go))mutedRoots.Add(go);go.SetActive(false);}
            }
        }
        void Start(){IsolateOtherScenes();if(initializationPending)InitializeWhenReady();}
        void InitializeWhenReady()
        {
            root=GetComponent<UIDocument>().rootVisualElement;if(root==null){initializationPending=true;return;}initializationPending=false;
            root.Clear();GetComponent<UIDocument>().visualTreeAsset.CloneTree(root);
            BuildUI();columnCamera.enabled=true;if(presentationCamera!=null)presentationCamera.enabled=true;
            if(initialCsv!=null)SetAnalysisResult(initialCsv.text,initialDocumentImage,initialSourceName);
        }
        void BuildUI()
        {
            viewport=root.Q("viewport");ruler=root.Q("ruler");labelLayer=root.Q("intervalLabels");
            if(viewport==null)throw new InvalidOperationException("Assign BoreholeViewer.uxml to the UI Document.");
            renderImage=root.Q<Image>("renderImage");renderImage.scaleMode=ScaleMode.StretchToFill;
            filename=root.Q<Label>("filename");range=root.Q<Label>("range");error=root.Q<Label>("error");status=root.Q<Label>("status");
            selectedName=root.Q<Label>("selectedName");selectedDepths=root.Q<Label>("selectedDepths");selectedInfo=root.Q<Label>("selectedInfo");depthReadout=root.Q<Label>("depthReadout");
            foreach(var text in root.Query<TextElement>().ToList())text.enableRichText=false;
            legends=new BoreholeLegends(root);documentPane=new BoreholeDocumentPane(this,root);csvOverlay=new BoreholeCsvOverlay(this,root);
            cutaway=GetComponent<BoreholeCutaway>();if(cutaway==null)cutaway=gameObject.AddComponent<BoreholeCutaway>();cutaway.Initialize(column,columnCamera);
            zoomSlider=root.Q<Slider>("zoomSlider");zoomPercent=root.Q<Label>("zoomPercent");
            zoomSlider.RegisterValueChangedCallback(e=>{if(column.Document==null)return;double target=column.Document.SpanFeet*1.4*Math.Pow(1d/1400,e.newValue);Zoom(target/WindowFeet);});
            root.Q<Button>("fit").clicked+=FitColumn;
            root.Q<Button>("toggleAnnotations").SetEnabled(false);root.Q<Button>("toggleAnnotations").clicked+=()=>{annotationsVisible=!annotationsVisible;ApplyAnnotations();};
            var appearance=root.Q<DropdownField>("appearance");appearance.choices=new List<string>{"Textured lithology","Legend / patterns"};appearance.index=0;
            appearance.RegisterValueChangedCallback(e=>{displayMode=e.newValue=="Legend / patterns"?DisplayMode.Legend:DisplayMode.Texture;column.SetMode(displayMode);});
            ruler.Clear();labelLayer.Clear();ticks.Clear();intervalLabels.Clear();intervalConnectors.Clear();
            for(int i=0;i<16;i++){var tick=new Label();tick.AddToClassList("depth-marker");tick.pickingMode=PickingMode.Ignore;ruler.Add(tick);ticks.Add(tick);}
            for(int i=0;i<24;i++)
            {
                var connector=new VisualElement{name="intervalConnector"+i,pickingMode=PickingMode.Ignore};connector.AddToClassList("interval-connector");labelLayer.Add(connector);intervalConnectors.Add(connector);
                var label=new Button();label.enableRichText=false;label.AddToClassList("interval-label");label.RegisterCallback<PointerDownEvent>(e=>e.StopPropagation());label.clicked+=()=>{if(label.userData is int index)SelectInterval(index,false);};labelLayer.Add(label);intervalLabels.Add(label);
            }
            viewport.RegisterCallback<GeometryChangedEvent>(_=>{ResizeTarget();poseDirty=true;});
            viewport.RegisterCallback<WheelEvent>(e=>{if(e.ctrlKey)Zoom(Math.Pow(1.12,e.delta.y));else ScrollDepth(e.delta.y);e.StopPropagation();});
            viewport.RegisterCallback<PointerDownEvent>(e=>
            {
                if(e.button!=0)return;dragging=true;moved=false;pointerId=e.pointerId;pointerStart=pointerPrevious=e.localPosition;viewport.CapturePointer(pointerId);
            });
            viewport.RegisterCallback<PointerMoveEvent>(e=>
            {
                if(!dragging)return;Vector2 current=e.localPosition;var delta=current-pointerPrevious;pointerPrevious=current;
                if((current-pointerStart).sqrMagnitude>25)moved=true;
                if(moved)ScrollDepth(-delta.y*35/Mathf.Max(1,viewport.contentRect.height));
            });
            viewport.RegisterCallback<PointerUpEvent>(e=>
            {
                if(!dragging||e.pointerId!=pointerId)return;dragging=false;viewport.ReleasePointer(pointerId);
                if(!moved){var pos=e.localPosition;var rect=viewport.contentRect;var ray=columnCamera.ViewportPointToRay(new Vector3(pos.x/rect.width,1-pos.y/rect.height,0));int index=column.Pick(ray);if(index>=0)SelectInterval(index,false);}
            });
            viewport.RegisterCallback<PointerCaptureOutEvent>(_=>dragging=false);
            SetDocumentMode(false);
        }
        public bool LoadCsvText(string csv,string sourceName="Backend CSV")
        {
            try
            {
                var document=BoreholeCsv.Parse(csv);ClearAnnotationSource();legends?.Clear();column.Build(document);column.SetMode(displayMode);csvOverlay?.Bind(document);SourceName=sourceName;PixelDisplacement=0;ReleaseDocumentSource();ReleaseOwnedImage();SegmentedImage=null;DocumentPixelWidth=DocumentPixelHeight=0;
                LastError=null;error.text="";error.AddToClassList("hidden");filename.text=sourceName;range.text=Format(document.TopFeet)+" – "+Format(document.BottomFeet)+" ft · "+document.SourceIntervalCount+" source intervals";
                WindowFeet=Math.Min(document.SpanFeet*1.15,Math.Max(Math.Min(100,document.SpanFeet),Math.Min(300,document.SpanFeet*.35)));FocusDepthFeet=document.TopFeet+WindowFeet*.5;SetDepth(FocusDepthFeet);UpdateDocumentImage();
                root.Q("selectionCard").AddToClassList("hidden");selectedName.text="";selectedDepths.text="";selectedInfo.text="";SetDocumentMode(false);
                int gaps=document.Intervals.Count-document.SourceIntervalCount;int unknown=0;foreach(var row in document.Intervals)if(!row.IsGap&&!column.catalog.Resolve(row.Lithology).IsResolved)unknown++;
                status.text=document.SourceIntervalCount+" intervals  /  "+gaps+" no-data gaps  /  "+unknown+" unrecognized labels  /  1 column · schematic scale";
                if(document.Warnings.Count>0){error.text=string.Join("\n",document.Warnings);error.RemoveFromClassList("hidden");}
                return true;
            }
            catch(Exception ex)when(ex is FormatException||ex is ArgumentException||ex is InvalidOperationException){ShowError(ex.Message);return false;}
        }
        public bool LoadCsvFile(string path)
        {
            try
            {
                if(string.IsNullOrWhiteSpace(path))throw new IOException("Choose a CSV file first.");
                var info=new FileInfo(path);if(!info.Exists)throw new IOException("The CSV file does not exist.");if(info.Length>BoreholeCsv.MaximumCharacters)throw new IOException("The CSV exceeds the 10 MB file limit.");
                return LoadCsvText(File.ReadAllText(path),Path.GetFileName(path));
            }
            catch(Exception ex)when(ex is IOException||ex is UnauthorizedAccessException||ex is ArgumentException){ShowError(ex.Message);return false;}
        }
        void OpenCsv(){try{string path=DebugCsvFileDialog.Open();if(!string.IsNullOrEmpty(path))LoadCsvFile(path);}catch(Exception ex){ShowError(ex.Message);}}
        void OpenImage(){try{string path=DebugCsvFileDialog.OpenImage();if(!string.IsNullOrEmpty(path))LoadDocumentFile(path);}catch(Exception ex){ShowError(ex.Message);}}
        public bool LoadDocumentFile(string path)
        {
            Texture2D loaded=null;
            try{var page=BoreholeDocumentImage.Load(path);loaded=page.Texture;ReleaseDocumentSource();ReleaseOwnedImage();ownedImage=loaded;SegmentedImage=loaded;DocumentPixelWidth=page.OriginalWidth;DocumentPixelHeight=page.OriginalHeight;SetDocumentSource(path);UpdateDocumentImage();poseDirty=true;return true;}
            catch(Exception ex){if(loaded!=null)Destroy(loaded);ShowError(ex.Message);return false;}
        }
        void ReleaseOwnedImage(){if(ownedImage==null)return;if(Application.isPlaying)Destroy(ownedImage);else DestroyImmediate(ownedImage);ownedImage=null;}
        /// <summary>Backend integration point. CSV and image must refer to the same analysis result. Image ownership stays with the caller.</summary>
        public bool SetAnalysisResult(string csv,Texture2D segmentedPage,string sourceName="Analysis result",int originalImageWidth=0,int originalImageHeight=0,double pixelDisplacement=0,string sourceImagePath=null)
        {
            if(originalImageWidth<0||originalImageHeight<0||(originalImageWidth>0)!=(originalImageHeight>0)){ShowError("Provide both original image dimensions, or omit both for an original-size texture.");return false;}
            if(!BoreholeCsv.Finite(pixelDisplacement)||pixelDisplacement<0){ShowError("Invalid image displacement.");return false;}
            if(!LoadCsvText(csv,sourceName))return false;if(!string.IsNullOrEmpty(sourceImagePath)&&!SetDocumentSource(sourceImagePath))return false;PixelDisplacement=pixelDisplacement;SegmentedImage=segmentedPage;DocumentPixelWidth=originalImageWidth>0?originalImageWidth:segmentedPage!=null?segmentedPage.width:0;DocumentPixelHeight=originalImageHeight>0?originalImageHeight:segmentedPage!=null?segmentedPage.height:0;UpdateDocumentImage();return true;
        }
        bool SetDocumentSource(string path)
        {
            string owned=Path.Combine(Application.temporaryCachePath,"document-source-"+Guid.NewGuid()+Path.GetExtension(path));
            try{File.Copy(path,owned);ReleaseDocumentSource();FullResolutionDocumentPath=owned;return true;}
            catch(Exception ex){DeleteDocumentSource(owned);ShowError("Cannot retain original document: "+ex.Message);return false;}
        }
        static void DeleteDocumentSource(string path)
        {try{if(!string.IsNullOrEmpty(path)&&File.Exists(path))File.Delete(path);}catch(IOException){}catch(UnauthorizedAccessException){}}
        void ReleaseDocumentSource()
        {
            string path=FullResolutionDocumentPath;var pending=documentPane?.PendingDocumentWork;documentPane?.ClearTiles();FullResolutionDocumentPath=null;
            if(pending!=null&&!pending.IsCompleted)pending.ContinueWith(_=>DeleteDocumentSource(path));else DeleteDocumentSource(path);
        }
        void UpdateDocumentImage()
        {
            documentPane?.Bind(column.Document,SegmentedImage,DocumentPixelWidth,DocumentPixelHeight);poseDirty=true;
        }
        public void SetDocumentMode(bool document)
        {
            // Compatibility with older integrations: both panes now remain visible.
            poseDirty=true;
        }
        public void SelectInterval(int index,bool focus=true)
        {
            var data=column.Document;if(data==null||index<0||index>=data.Intervals.Count)return;column.Select(index);root.Q("selectionCard").RemoveFromClassList("hidden");var row=data.Intervals[index];selectedName.text=Name(row);
            selectedDepths.text=Format(row.TopFeet)+" → "+Format(row.BottomFeet)+" ft  /  "+Format(row.ThicknessFeet)+" ft thick";
            var resolution=column.catalog.Resolve(row.IsGap?"Not Available":row.Lithology);var definition=resolution.definition;
            selectedInfo.text=row.IsGap?"No source interval covers this depth.":definition==null?"Unrecognized source label · schematic fallback": "Illustrative material · source row "+row.SourceRow;
            csvOverlay?.Reveal(index);if(focus)SetDepth((row.TopFeet+row.BottomFeet)*.5);poseDirty=true;
        }
        public void SetDepth(double feet,bool fromDocument=false)
        {
            if(column.Document==null||!BoreholeCsv.Finite(feet))return;var d=column.Document;
            FocusDepthFeet=Math.Max(d.TopFeet,Math.Min(d.BottomFeet,feet));poseDirty=true;
            if(!fromDocument)documentPane?.CenterOnDepth(FocusDepthFeet);

        }
        public void ScrollDepth(float wheelDelta){if(column.Document==null)return;if(DocumentSynchronized)documentPane.PanDepth(wheelDelta*WindowFeet/35);else SetDepth(FocusDepthFeet+wheelDelta*WindowFeet/35);}
        public void Orbit(float horizontal,float vertical){}
        public void Zoom(double factor)
        {
            if(column.Document==null||!BoreholeCsv.Finite(factor)||factor<=0)return;var d=column.Document;WindowFeet=Math.Max(d.SpanFeet/1000,Math.Min(d.SpanFeet*1.4,WindowFeet*factor));poseDirty=true;documentPane?.Refresh();
        }
        public void FitColumn(){if(column.Document==null)return;double gap=cutaway!=null?cutaway.SurfaceOffset:0;WindowFeet=column.Document.SpanFeet*(1.15+gap/column.displayHeight);SetDepth(column.Document.TopFeet+column.Document.SpanFeet*(.5-gap/(2*column.displayHeight)));documentPane?.Fit();}
        void UpdateZoomControl()
        {
            double span=column.Document.SpanFeet;double fit=span*(1.15+(cutaway!=null?cutaway.SurfaceOffset:0)/column.displayHeight);
            zoomSlider.SetValueWithoutNotify((float)(Math.Log(span*1.4/WindowFeet)/Math.Log(1400)));zoomPercent.text=Math.Round(fit/WindowFeet*100)+"%";
            zoomSlider.tooltip="Zoom · Fit = 100%";
        }
        void ResizeTarget()
        {
            if(!isActiveAndEnabled||viewport==null||DocumentMode)return;int w=Mathf.Clamp(Mathf.CeilToInt(viewport.contentRect.width),64,2048),h=Mathf.Clamp(Mathf.CeilToInt(viewport.contentRect.height),64,1600);
            if(texture!=null&&texture.width==w&&texture.height==h)return;ReleaseTarget();texture=new RenderTexture(w,h,24,RenderTextureFormat.ARGB32){name="Borehole viewport",hideFlags=HideFlags.DontSave,antiAliasing=2};texture.Create();columnCamera.targetTexture=texture;columnCamera.aspect=w/(float)h;renderImage.image=texture;
        }
        void LateUpdate()
        {
            AdvancingFrames++;documentPane?.Tick();if(initializationPending)InitializeWhenReady();if(!poseDirty||column.Document==null||DocumentMode)return;ResizeTarget();poseDirty=false;
            UpdateZoomControl();var map=column.DepthMap;columnCamera.orthographicSize=Mathf.Max(.000001f,(float)(WindowFeet/column.Document.SpanFeet)*column.displayHeight*.5f);
            bool atSurface=ShowingSurface;
            if(atSurface)columnCamera.orthographicSize=Mathf.Max(4.5f,columnCamera.orthographicSize);
            float zoomAmount=Mathf.Clamp01((float)(Math.Log(2500/WindowFeet)/Math.Log(2500/60d)));
            float widthFraction=Mathf.Lerp(.14f,.24f,zoomAmount);
            float radialScale=columnCamera.orthographicSize*columnCamera.aspect*widthFraction/column.radius;
            column.transform.localScale=new Vector3(radialScale,1,radialScale);
            cutaway?.SetWidth(radialScale);
            // Reference appearance: the 60 ft close-up shown in the material acceptance image.
            float referenceSize=(float)(60/column.Document.SpanFeet)*column.displayHeight*.5f;
            column.SetTextureDisplayScale(referenceSize/(1.4f*Mathf.Max(.00001f,columnCamera.orthographicSize)));
            column.SetHighlightWidth(columnCamera.orthographicSize*4/Mathf.Max(1,viewport.contentRect.height));
            var rotation=Quaternion.Euler(pitch,yaw,0);var focus=column.transform.TransformPoint(new Vector3(0,map.ToY(FocusDepthFeet)+(atSurface?(cutaway!=null?cutaway.SurfaceOffset:0)+columnCamera.orthographicSize*.1f:0),0));
            columnCamera.transform.SetPositionAndRotation(focus+rotation*Vector3.back*45,rotation);
            depthReadout.text="Depth "+Format(FocusDepthFeet)+" ft   /   visible "+Format(VisibleBottomFeet-VisibleTopFeet)+" ft";UpdateAnnotations();documentPane?.Refresh();
        }
        public void UpdateAnnotations()
        {
            float width=viewport.contentRect.width,height=viewport.contentRect.height;if(width<10||height<10)return;
            float ScreenY(double d)=>height*(1-columnCamera.WorldToViewportPoint(column.transform.TransformPoint(new Vector3(0,column.DepthMap.ToY(d),0))).y);
            double ideal=WindowFeet/8,power=Math.Pow(10,Math.Floor(Math.Log10(ideal))),v=ideal/power,step=(v<2?2:v<5?5:10)*power;
            double first=Math.Ceiling(Math.Max(column.Document.TopFeet,FocusDepthFeet-WindowFeet*.7)/step)*step;int used=0;
            for(double d=first;d<=column.Document.BottomFeet&&d<=FocusDepthFeet+WindowFeet*.7&&used<ticks.Count;d+=step)
            {float y=ScreenY(d);if(y<70||y>height-75)continue;var tick=ticks[used++];tick.style.display=DisplayStyle.Flex;tick.style.top=y;tick.text=Format(d);}
            for(int i=used;i<ticks.Count;i++)ticks[i].style.display=DisplayStyle.None;
            var gapLabel=root.Q<Label>("surfaceGap");float gap=cutaway!=null?cutaway.SurfaceOffset:0;
            float gapY=height*(1-columnCamera.WorldToViewportPoint(column.transform.TransformPoint(new Vector3(0,gap*.5f,0))).y);
            gapLabel.EnableInClassList("hidden",gap<=0||gapY<30||gapY>height-100);gapLabel.text="0 → "+Format(column.Document.TopFeet)+" ft\nDepth break · not to scale";gapLabel.style.top=gapY;
            var candidates=new List<int>();var positions=new List<float>();var anchors=new List<float>();
            float selectedY=float.NaN;
            for(int i=0;i<column.Document.Intervals.Count;i++)
            {
                var row=column.Document.Intervals[i];float midpoint=ScreenY((row.TopFeet+row.BottomFeet)*.5);float y=midpoint,anchor=midpoint;
                if(i==column.SelectedIndex)
                {
                    double a=Math.Max(row.TopFeet,VisibleTopFeet),b=Math.Min(row.BottomFeet,VisibleBottomFeet);
                    if(b>a)y=anchor=ScreenY((a+b)*.5);
                    y=Mathf.Clamp(y,70,Mathf.Max(70,height-100));selectedY=y;
                }
                else if(y<70||y>height-100)continue;
                candidates.Add(i);positions.Add(y);anchors.Add(anchor);
            }
            bool hide=WindowFeet>=column.Document.SpanFeet*.6||candidates.Count>12;
            float[] previous={float.NegativeInfinity,float.NegativeInfinity};
            for(int n=0;n<candidates.Count;n++)
            {
                if(candidates[n]==column.SelectedIndex)continue;int side=candidates[n]%2;
                if(positions[n]-previous[side]<46)hide=true;previous[side]=positions[n];
            }
            root.Q("labelHint").EnableInClassList("hidden",!hide);
            used=0;
            for(int n=0;n<candidates.Count&&used<intervalLabels.Count;n++)
            {
                int i=candidates[n];bool selected=i==column.SelectedIndex,left=i%2==0;
                if(!selected&&(hide||(!float.IsNaN(selectedY)&&i%2==column.SelectedIndex%2&&Mathf.Abs(positions[n]-selectedY)<46)))continue;
                var row=column.Document.Intervals[i];var label=intervalLabels[used++];
                label.style.display=DisplayStyle.Flex;label.style.left=left?8:width*.62f;label.style.width=left?width*.28f:Mathf.Min(195,width*.36f);label.style.top=positions[n]-21;
                string direction=selected&&anchors[n]<0?" ↑":selected&&anchors[n]>height?" ↓":"";
                label.text=Name(row)+direction+"\n"+Format(row.TopFeet)+"–"+Format(row.BottomFeet)+" ft";label.userData=i;label.style.borderLeftWidth=3;label.style.borderLeftColor=column.IdentifierColors[i];label.EnableInClassList("active",selected);
                var connector=intervalConnectors[used-1];float anchorX=width*(left?.415f:.585f),edge=left?8+width*.28f:width*.62f;
                float startX=left?edge:anchorX,endX=left?anchorX:edge,startY=left?positions[n]:anchors[n],endY=left?anchors[n]:positions[n];
                connector.style.display=DisplayStyle.Flex;connector.style.left=startX;connector.style.top=startY-1;
                connector.style.width=Vector2.Distance(new Vector2(startX,startY),new Vector2(endX,endY));
                connector.style.transformOrigin=new TransformOrigin(Length.Percent(0),Length.Percent(50),0);
                connector.style.rotate=new Rotate(new Angle(Mathf.Atan2(endY-startY,endX-startX)*Mathf.Rad2Deg,AngleUnit.Degree));
                connector.style.backgroundColor=selected?new Color(.47f,1,0):new Color(.86f,.80f,.72f);
            }
            foreach(var tick in ticks){tick.style.left=width*.30f;tick.style.width=width*.10f;}
            for(int i=used;i<intervalLabels.Count;i++){intervalLabels[i].style.display=DisplayStyle.None;intervalConnectors[i].style.display=DisplayStyle.None;}
        }
        void ShowError(string message){LastError=message;if(error!=null){error.text=message;error.RemoveFromClassList("hidden");}}
        static string Name(BoreholeInterval row)=>string.IsNullOrWhiteSpace(row.Lithology)?"Unspecified lithology":row.Lithology;
        public static string Format(double value)=>value.ToString("N2",CultureInfo.InvariantCulture).TrimEnd('0').TrimEnd('.');
        void ReleaseTarget(){if(texture==null)return;if(columnCamera!=null)columnCamera.targetTexture=null;texture.Release();if(Application.isPlaying)Destroy(texture);else DestroyImmediate(texture);texture=null;}
        void OnDisable()
        {
            if(columnCamera!=null)columnCamera.enabled=false;if(presentationCamera!=null)presentationCamera.enabled=false;legends?.Dispose();legends=null;ClearAnnotationSource();ReleaseTarget();ReleaseDocumentSource();ReleaseOwnedImage();foreach(var go in mutedRoots)if(go!=null)go.SetActive(true);mutedRoots.Clear();
        }
    }
}
