using System;
using UnityEngine;
using UnityEngine.UIElements;
namespace Subsurface.Borehole
{
    public sealed class BoreholeDocumentPane
    {
        readonly BoreholeViewer viewer;readonly VisualElement viewport,window,placeholder,selection;readonly Image image;readonly Label state,notice,readout;
        BoreholeImageMap map;Texture2D texture;float scale;double center;bool initialized,dragging;int pointer;float previousY;string problem;int originalWidth,originalHeight;
        public bool IsSynchronized{get;private set;}
        readonly BoreholeDocumentTiles tiles;
        public System.Threading.Tasks.Task PendingDocumentWork=>tiles.PendingWork;
        public BoreholeDocumentTiles Tiles=>tiles;
        public void ClearTiles()=>tiles.Clear();
        public void Tick()
        {
            tiles.Tick();
            if(!string.IsNullOrEmpty(tiles.Error)){notice.text="Full-resolution detail unavailable: "+tiles.Error;notice.RemoveFromClassList("hidden");}
        }
        public bool AtHeader=>IsSynchronized&&center<map.FirstPixel;
        public BoreholeDocumentPane(BoreholeViewer viewer,VisualElement root)
        {
            this.viewer=viewer;viewport=root.Q("documentViewport");window=root.Q("documentWindow");selection=root.Q("documentSelection");placeholder=root.Q("documentPlaceholder");image=root.Q<Image>("documentImage");state=root.Q<Label>("syncState");notice=root.Q<Label>("documentNotice");readout=root.Q<Label>("pixelReadout");image.scaleMode=ScaleMode.StretchToFill;
            tiles=new BoreholeDocumentTiles(root.Q("documentTiles"));
            viewport.RegisterCallback<GeometryChangedEvent>(_=>Refresh());
            viewport.RegisterCallback<WheelEvent>(e=>{if(e.ctrlKey)viewer.Zoom(Math.Pow(1.12,e.delta.y));else Pan(e.delta.y*20);e.StopPropagation();});
            viewport.RegisterCallback<PointerDownEvent>(e=>{if(e.button!=0)return;dragging=true;pointer=e.pointerId;previousY=e.localPosition.y;viewport.CapturePointer(pointer);});
            viewport.RegisterCallback<PointerMoveEvent>(e=>{if(!dragging||pointer!=e.pointerId)return;float y=e.localPosition.y;Pan(previousY-y);previousY=y;});
            viewport.RegisterCallback<PointerUpEvent>(e=>{if(e.pointerId!=pointer)return;dragging=false;viewport.ReleasePointer(pointer);});viewport.RegisterCallback<PointerCaptureOutEvent>(_=>dragging=false);
        }
        public void Bind(BoreholeDocument data,Texture2D page,int width,int height)
        {
            tiles.Clear();
            texture=page;image.image=page;image.EnableInClassList("hidden",page==null);originalWidth=width;originalHeight=height;map=null;problem=null;IsSynchronized=false;initialized=false;
            if(data==null)problem="Load a CSV.";else if(!BoreholeImageMap.TryCreate(data,out map,out problem,viewer.PixelDisplacement)){}else if(page==null)problem="Load the matching document.";else if(width<=0||height<=0||map.LastPixel>height)problem="Image size mismatch. Load the matching original document; synchronization is paused.";else IsSynchronized=true;
            center=IsSynchronized?map.ToPixel(viewer.FocusDepthFeet):0;initialized=true;placeholder.EnableInClassList("hidden",page!=null);state.text=IsSynchronized?"Linked":"Unlinked";Refresh();
        }
        public void CenterOnDepth(double feet){if(IsSynchronized)center=map.ToPixel(feet);Refresh();}
        public void Fit(){center=originalHeight*.5;Refresh();}
        public void PanDepth(double feet)
        {
            if(!IsSynchronized)return;double ratio=(map.LastPixel-map.FirstPixel)/viewer.column.Document.SpanFeet;
            double delta=map.ToPixel(viewer.FocusDepthFeet+feet)-map.ToPixel(viewer.FocusDepthFeet);if(AtHeader||Math.Abs(delta)<.00001)delta=feet*ratio;Pan((float)(delta*scale));
        }
        void Pan(float amount)
        {
            if(texture==null||scale<=0)return;center=Math.Max(0,Math.Min(originalHeight,center+amount/scale));
            if(IsSynchronized)viewer.SetDepth(map.ToDepth(center),true);Refresh();
        }
        public void Refresh()
        {
            if(!initialized)return;notice.text=problem??"";notice.EnableInClassList("hidden",string.IsNullOrEmpty(problem));window.style.display=DisplayStyle.None;selection.style.display=DisplayStyle.None;if(texture==null)return;
            float width=viewport.contentRect.width,height=viewport.contentRect.height;if(width<1||height<1)return;
            float fit=Mathf.Min(width/originalWidth,height/originalHeight),zoom=viewer.column.Document==null?1:(float)(viewer.column.Document.SpanFeet*1.15/Math.Max(.0001,viewer.WindowFeet));
            scale=Mathf.Min(width/originalWidth,fit*zoom);
            if(IsSynchronized&&!AtHeader){double span=map.ToPixel(viewer.VisibleBottomFeet)-map.ToPixel(viewer.VisibleTopFeet);if(span>0)scale=Mathf.Min(scale,(float)(height/span));}
            float drawnWidth=originalWidth*scale,drawnHeight=originalHeight*scale,left=(width-drawnWidth)*.5f;
            float offset=drawnHeight<=height?(height-drawnHeight)*.5f:Mathf.Clamp(height*.5f-(float)center*scale,height-drawnHeight,0);
            image.style.left=left;image.style.top=offset;image.style.width=drawnWidth;image.style.height=drawnHeight;
            tiles.SetView(viewer.FullResolutionDocumentPath,originalWidth,originalHeight,left,offset,scale,width,height);
            readout.tooltip="Source image resolution · detail tiles load as you navigate";
            if(IsSynchronized&&!viewer.ShowingSurface&&drawnWidth>=width-.5f&&drawnHeight>height+.5f)
            {
                double top=map.ToPixel(viewer.VisibleTopFeet),bottom=map.ToPixel(viewer.VisibleBottomFeet);float h=(float)((bottom-top)*scale);
                if(h<height){window.style.display=DisplayStyle.Flex;window.style.left=left;window.style.width=drawnWidth;window.style.top=offset+(float)top*scale;window.style.height=h;window.style.borderTopWidth=window.style.borderBottomWidth=Mathf.Min(4,h*.25f);}
            }
            int selected=viewer.column.SelectedIndex;
            if(IsSynchronized&&selected>=0&&selected<viewer.column.Document.Intervals.Count)
            {
                var row=viewer.column.Document.Intervals[selected];
                if(!row.IsGap&&row.HasImageCoordinates)
                {
                    float top=offset+(float)((row.TopPixel+viewer.PixelDisplacement)*scale),h=(float)((row.BottomPixel-row.TopPixel)*scale);
                    if(top<height&&top+h>0)
                    {
                        selection.style.display=DisplayStyle.Flex;selection.style.left=left;selection.style.width=drawnWidth;selection.style.top=top;selection.style.height=h;
                        selection.style.borderTopWidth=selection.style.borderBottomWidth=Mathf.Min(2,h*.25f);
                    }
                }
            }
            readout.text=Math.Round(scale*100)+"%";
        }
    }
}
