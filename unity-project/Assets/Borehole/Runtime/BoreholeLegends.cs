using System;
using UnityEngine;
using UnityEngine.UIElements;
namespace Subsurface.Borehole
{
    public sealed class BoreholeLegends : IDisposable
    {
        readonly VisualElement panel;readonly Texture2D[] textures=new Texture2D[2];
        public BoreholeLegends(VisualElement root)
        {
            panel=new VisualElement{name="documentLegends"};panel.AddToClassList("document-legends");var viewport=root.Q("documentViewport");viewport.Insert(viewport.IndexOf(root.Q("csvOverlay")),panel);
            panel.RegisterCallback<WheelEvent>(e=>e.StopPropagation());panel.RegisterCallback<PointerDownEvent>(e=>e.StopPropagation());
        }
        public void Bind(byte[] original,byte[] generated)
        {
            Clear();Add(0,"Original legend",original);Add(1,"Generated legend",generated);
        }
        void Add(int index,string title,byte[] data)
        {
            var foldout=new Foldout{text=title,value=false};panel.Add(foldout);
            foldout.RegisterValueChangedCallback(_=>panel.EnableInClassList("legends-expanded",panel.Query<Foldout>().ToList().Exists(x=>x.value)));
            if(data==null){var label=new Label("Not included in this result. Reprocess the document to obtain this legend.");label.AddToClassList("legend-notice");foldout.Add(label);return;}
            Texture2D texture=null;
            try
            {
                texture=new Texture2D(2,2);if(!texture.LoadImage(data))throw new FormatException("Invalid legend PNG.");textures[index]=texture;
                var scroll=new ScrollView(ScrollViewMode.Vertical);scroll.AddToClassList("legend-scroll");var image=new Image{image=texture,scaleMode=ScaleMode.ScaleToFit};image.AddToClassList("legend-image");scroll.Add(image);
                Action resize=()=>{float width=scroll.contentViewport.contentRect.width;if(width<=0)return;image.style.width=width;image.style.height=width*texture.height/texture.width;};
                foldout.Add(scroll);
                scroll.contentViewport.RegisterCallback<GeometryChangedEvent>(_=>resize());resize();
            }
            catch(Exception){if(texture!=null)UnityEngine.Object.Destroy(texture);textures[index]=null;foldout.Add(new Label("This legend could not be decoded."));}
        }
        public void Clear(){panel.Clear();panel.RemoveFromClassList("legends-expanded");for(int i=0;i<2;i++)if(textures[i]!=null){if(Application.isPlaying)UnityEngine.Object.Destroy(textures[i]);else UnityEngine.Object.DestroyImmediate(textures[i]);textures[i]=null;}}
        public void Dispose(){Clear();panel.RemoveFromHierarchy();}
    }
}
