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
        bool focusMode;float focusZoom=1,focusPanX,previousX;double splitCenter;
        public void SetFocus(bool value){if(value){splitCenter=center;focusZoom=1;focusPanX=0;}else center=splitCenter;focusMode=value;Refresh();}
        Vector2 pressPosition;bool pointerMoved;float imageLeft,imageTop,imageWidth;int hovered=-1;
        public int IntervalAt(Vector2 position)
        {
            if(!IsSynchronized||scale<=0||position.x<imageLeft||position.x>imageLeft+imageWidth)return -1;
            return BoreholeImageMap.IntervalAtPixel(viewer.column.Document,(position.y-imageTop)/scale,viewer.PixelDisplacement);
        }
        void Hover(Vector2 position){hovered=IntervalAt(position);viewport.tooltip=hovered>=0?"Select "+viewer.column.Document.Intervals[hovered].Lithology+" · click or press Enter":IsSynchronized?"No mapped interval here":"Selection unavailable: document synchronization is unlinked";viewport.EnableInClassList("mapped-region-hover",hovered>=0);}
        public bool AtHeader=>IsSynchronized&&center<map.FirstPixel;
        public BoreholeDocumentPane(BoreholeViewer viewer,VisualElement root)
        {
            this.viewer=viewer;viewport=root.Q("documentViewport");window=root.Q("documentWindow");selection=root.Q("documentSelection");placeholder=root.Q("documentPlaceholder");image=root.Q<Image>("documentImage");state=root.Q<Label>("syncState");notice=root.Q<Label>("documentNotice");readout=root.Q<Label>("pixelReadout");image.scaleMode=ScaleMode.StretchToFill;
            viewport.focusable=true;viewport.tooltip="Document regions: use Up/Down to browse mapped intervals, Enter to select.";viewport.RegisterCallback<KeyDownEvent>(e=>{if(!IsSynchronized)return;if(e.keyCode==KeyCode.UpArrow||e.keyCode==KeyCode.DownArrow){var rows=viewer.column.Document.Intervals;int direction=e.keyCode==KeyCode.UpArrow?-1:1;int start=hovered>=0?hovered:viewer.column.SelectedIndex;for(int i=start+direction;i>=0&&i<rows.Count;i+=direction)if(!rows[i].IsGap&&rows[i].HasImageCoordinates){hovered=i;viewport.tooltip="Press Enter to select "+rows[i].Lithology;break;}e.StopPropagation();}else if(e.keyCode==KeyCode.Return&&hovered>=0){viewer.SelectInterval(hovered,true);e.StopPropagation();}});viewport.RegisterCallback<PointerLeaveEvent>(_=>{viewport.RemoveFromClassList("mapped-region-hover");});
            tiles=new BoreholeDocumentTiles(root.Q("documentTiles"));
            viewport.RegisterCallback<GeometryChangedEvent>(_=>Refresh());
            viewport.RegisterCallback<WheelEvent>(e=>{if(e.ctrlKey){if(focusMode){focusZoom=Mathf.Clamp(focusZoom/(float)Math.Pow(1.12,e.delta.y),.1f,20);Refresh();}else viewer.Zoom(Math.Pow(1.12,e.delta.y));}else Pan(e.delta.y*20);e.StopPropagation();});
            viewport.RegisterCallback<PointerDownEvent>(e=>{if(e.button!=0)return;dragging=true;pointer=e.pointerId;previousY=e.localPosition.y;previousX=e.localPosition.x;pressPosition=e.localPosition;pointerMoved=false;viewport.CapturePointer(pointer);});
            viewport.RegisterCallback<PointerMoveEvent>(e=>{Hover(e.localPosition);if(!dragging||pointer!=e.pointerId)return;if(((Vector2)e.localPosition-pressPosition).sqrMagnitude>25)pointerMoved=true;if(!pointerMoved)return;float y=e.localPosition.y;if(focusMode){focusPanX+=e.localPosition.x-previousX;previousX=e.localPosition.x;}Pan(previousY-y);previousY=y;});
            viewport.RegisterCallback<PointerUpEvent>(e=>{if(!dragging||e.pointerId!=pointer)return;dragging=false;viewport.ReleasePointer(pointer);if(!pointerMoved){int index=IntervalAt(e.localPosition);if(index>=0)viewer.SelectInterval(index,true);}});viewport.RegisterCallback<PointerCaptureOutEvent>(_=>dragging=false);
        }
        public void Bind(BoreholeDocument data,Texture2D page,int width,int height)
        {
            tiles.Clear();
            texture=page;image.image=page;image.EnableInClassList("hidden",page==null);originalWidth=width;originalHeight=height;map=null;problem=null;IsSynchronized=false;initialized=false;
            if(data==null)problem="Load a CSV.";else if(!BoreholeImageMap.TryCreate(data,out map,out problem,viewer.PixelDisplacement)){}else if(page==null)problem="Load the matching document.";else if(width<=0||height<=0||map.LastPixel>height)problem="Image size mismatch. Load the matching original document; synchronization is paused.";else IsSynchronized=true;
            center=IsSynchronized?map.ToPixel(viewer.FocusDepthFeet):0;initialized=true;placeholder.EnableInClassList("hidden",page!=null);state.text=IsSynchronized?"Linked":"Unlinked";Refresh();
        }
        public void ReplacePage(BoreholeDocument data,Texture2D page,int width,int height,double previousOffset)
        {double previousCenter=center;Bind(data,page,width,height);center=previousCenter+viewer.PixelDisplacement-previousOffset;Refresh();}
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
            if(IsSynchronized&&!focusMode)viewer.SetDepth(map.ToDepth(center),true);Refresh();
        }
        public void Refresh()
        {
            if(!initialized)return;notice.text=problem??"";notice.EnableInClassList("hidden",string.IsNullOrEmpty(problem));window.style.display=DisplayStyle.None;selection.style.display=DisplayStyle.None;if(texture==null)return;
            float width=viewport.contentRect.width,height=viewport.contentRect.height;if(width<1||height<1)return;
            float fit=Mathf.Min(width/originalWidth,height/originalHeight),zoom=viewer.column.Document==null?1:(float)(viewer.column.Document.SpanFeet*1.15/Math.Max(.0001,viewer.WindowFeet));
            scale=focusMode?width/originalWidth*focusZoom:Mathf.Min(width/originalWidth,fit*zoom);
            if(!focusMode&&IsSynchronized&&!AtHeader){double span=map.ToPixel(viewer.VisibleBottomFeet)-map.ToPixel(viewer.VisibleTopFeet);if(span>0)scale=Mathf.Min(scale,(float)(height/span));}
            float drawnWidth=originalWidth*scale,drawnHeight=originalHeight*scale,left=(width-drawnWidth)*.5f;
            if(focusMode&&drawnWidth>width)left=Mathf.Clamp(left+focusPanX,width-drawnWidth,0);
            float offset=drawnHeight<=height?(height-drawnHeight)*.5f:Mathf.Clamp(height*.5f-(float)center*scale,height-drawnHeight,0);
            imageLeft=left;imageTop=offset;imageWidth=drawnWidth;
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
