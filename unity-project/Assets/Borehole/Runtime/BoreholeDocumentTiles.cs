using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.UIElements;

namespace Subsurface.Borehole
{
    /// <summary>Bounded native-resolution tiles; decoding runs off the Unity thread.</summary>
    public sealed class BoreholeDocumentTiles
    {
        const int TileSize=1024,MaximumTiles=24;
        sealed class Tile {public RectInt Rectangle;public Image Image;public Texture2D Texture;public int Used;}
        readonly VisualElement layer;
        readonly Dictionary<Vector2Int,Tile> cache=new Dictionary<Vector2Int,Tile>();
        readonly List<Vector2Int> visible=new List<Vector2Int>();
        Task<BoreholeDocumentImage.Pixels> decoding;
        Vector2Int loading;int generation,loadingGeneration,clock,width,height;
        string path;float left,top,scale;int sample=1;
        int Step=>TileSize*sample;
        public int SourcePixelsPerTexel=>sample;
        public Task PendingWork=>decoding;
        public string Error {get;private set;}
        public int ResidentTiles=>cache.Count;
        public bool Ready {get;private set;}
        public BoreholeDocumentTiles(VisualElement layer){this.layer=layer;}
        public void Clear()
        {
            generation++;path=null;visible.Clear();foreach(var tile in cache.Values)Release(tile);cache.Clear();layer.Clear();Error=null;Ready=false;
        }
        public void SetView(string source,int sourceWidth,int sourceHeight,float x,float y,float zoom,float viewportWidth,float viewportHeight)
        {
            int requested=1;while(requested<1024&&requested*2*zoom<=1)requested*=2;
            if(path!=source||requested!=sample){Clear();path=source;sample=requested;}
            width=sourceWidth;height=sourceHeight;left=x;top=y;scale=zoom;visible.Clear();clock++;
            if(string.IsNullOrEmpty(path)||scale<=0)return;
            int minX=Math.Max(0,(int)Math.Floor(-left/scale/Step)),maxX=Math.Min((width-1)/Step,(int)Math.Floor((viewportWidth-left)/scale/Step));
            int minY=Math.Max(0,(int)Math.Floor(-top/scale/Step)),maxY=Math.Min((height-1)/Step,(int)Math.Floor((viewportHeight-top)/scale/Step));
            // At overview scales, the preview already exceeds the viewport's pixel density.
            if((long)(maxX-minX+1)*(maxY-minY+1)>MaximumTiles){Hide();return;}
            foreach(var tile in cache.Values)tile.Image.style.display=DisplayStyle.None;
            for(int row=minY;row<=maxY;row++)for(int col=minX;col<=maxX;col++)
            {
                var key=new Vector2Int(col,row);visible.Add(key);if(cache.TryGetValue(key,out var tile)){tile.Used=clock;Position(tile);}
            }
            Ready=visible.Count>0&&visible.TrueForAll(key=>cache.ContainsKey(key));
        }
        void Hide(){visible.Clear();Ready=false;foreach(var tile in cache.Values)tile.Image.style.display=DisplayStyle.None;}
        RectInt Rectangle(Vector2Int key)=>new RectInt(key.x*Step,key.y*Step,Math.Min(Step,width-key.x*Step),Math.Min(Step,height-key.y*Step));
        void Position(Tile tile)
        {
            var rect=tile.Rectangle;tile.Image.style.display=DisplayStyle.Flex;tile.Image.style.left=left+rect.x*scale;tile.Image.style.top=top+rect.y*scale;tile.Image.style.width=rect.width*scale;tile.Image.style.height=rect.height*scale;
        }
        public void Tick()
        {
            if(decoding!=null)
            {
                if(!decoding.IsCompleted)return;
                if(decoding.IsFaulted){if(loadingGeneration==generation)Error=decoding.Exception.GetBaseException().Message;}
                else if(!decoding.IsCanceled&&loadingGeneration==generation&&visible.Contains(loading))
                {
                    if(cache.Count>=MaximumTiles)
                    {
                        Vector2Int oldest=default;int age=int.MaxValue;bool found=false;
                        foreach(var pair in cache)if(!visible.Contains(pair.Key)&&pair.Value.Used<age){oldest=pair.Key;age=pair.Value.Used;found=true;}
                        if(found){Release(cache[oldest]);cache.Remove(oldest);}
                    }
                    if(cache.Count<MaximumTiles)
                    {
                        var pixels=decoding.Result;var tile=new Tile{Rectangle=Rectangle(loading),Texture=pixels.CreateTexture(),Used=clock};
                        tile.Image=new Image{image=tile.Texture,scaleMode=ScaleMode.StretchToFill,pickingMode=PickingMode.Ignore};tile.Image.AddToClassList("document-tile");layer.Add(tile.Image);cache.Add(loading,tile);Position(tile);
                    }
                }
                decoding=null;
            }
            Ready=visible.Count>0&&visible.TrueForAll(key=>cache.ContainsKey(key));
            if(Error!=null||string.IsNullOrEmpty(path))return;
            foreach(var key in visible)if(!cache.ContainsKey(key))
            {
                loading=key;loadingGeneration=generation;var rectangle=Rectangle(key);string source=path;
                int level=sample;decoding=Task.Run(()=>BoreholeDocumentImage.ReadRegion(source,rectangle,level));break;
            }
        }
        static void Release(Tile tile){tile.Image.RemoveFromHierarchy();if(tile.Texture!=null){if(Application.isPlaying)UnityEngine.Object.Destroy(tile.Texture);else UnityEngine.Object.DestroyImmediate(tile.Texture);}}
    }
}
